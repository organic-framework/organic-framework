"""`of tools`: a position's tools (MCP servers, plugins, connectors) bound to the position, not to the Claude account
(docs/tools.md; RFC 0004). Owner: of_tools.

  of tools inventory [--days 14] [--json]
      Read-only, names only: per Claude config folder the installed and enabled plugins, the MCP servers by scope and
      the claude.ai connectors ever connected; Codex's MCP servers; project .mcp.json files under the organizations'
      paths; and which positions called which MCP server in the last N days (the local store). No value is read out:
      an env or header entry is shown by its name, a credential file by its path only.
  of tools check
      Validate every listed registry's `tools:` catalogue and the positions' `tools:` lists; exit 1 on problems.
  of tools plan <position> [--config-dir D] [--json]
      The session config the launcher generates for the position on that account: the --mcp-config file (the
      position's portable MCP servers), and with `tools_mode: enforce` the --settings keys (allowedMcpServers, enabledPlugins). Nothing holds a
      secret: a credential reaches only the MCP server's own process, through `of secret run` at its start.

How a session is bound (Claude Code, checked on 2.1.322, 2026-11-11; docs/tools.md §4 has the probes):
  - `allowedMcpServers` in the session's --settings filters every source: user, project, plugin, claude.ai
    connectors and --mcp-config servers. A server passes by `serverName` (user and --mcp-config servers) or by
    `serverUrl` (remote ones: a plugin's or a connector's endpoint). An empty list allows none.
  - `enabledPlugins` in --settings merges per entry with the account's own, so every plugin installed on the
    account is named, true or false.
  - A remote server's OAuth sign-in stays with the account and the definition that made it: the same URL under a
    --mcp-config definition is not signed in. So an OAuth tool (a plugin's or a connector's) is account_bound, and
    `--strict-mcp-config` is not used: it would hide them.
"""
import argparse
import json
import os
import re
import secrets
import shlex
import socket
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

KINDS = ("mcp-stdio", "mcp-remote", "plugin", "connector")
SCOPES = ("read", "write", "act-as-person")
ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SECRETISH = re.compile(r"(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|PRIVATE|AUTH|COOKIE|SESSION)", re.I)
REF_LIKE = re.compile(r"^((\$\{[A-Z_][A-Z0-9_]*(:-[^}]*)?\}|\$[A-Z_][A-Z0-9_]*)(/.*)?|op://.*|~?/.*)$")
STATE_DIR = os.environ.get("OF_STATE") or os.path.expanduser("~/.local/state/of")


class ToolsError(Exception):
    pass


# ---------- the catalogue and the profile ----------

def registries(orgs_file=None):
    """{org: registry doc} for the organizations with a local checkout."""
    import yaml
    import of_credentials
    out = {}
    for org, path in of_credentials._registries(orgs_file):
        try:
            with open(path) as fh:
                out[org] = yaml.safe_load(fh) or {}
        except (OSError, Exception):  # noqa: BLE001 - a broken registry is `of registry check`'s finding
            continue
    return out


def _as_list(v):
    if v is None:
        return []
    return [str(x) for x in v] if isinstance(v, list) else [str(v)]


def find_position(name, docs):
    """(org, position dict, doc) of a canonical name or alias."""
    hits = []
    for org, doc in docs.items():
        for p in doc.get("positions") or []:
            if isinstance(p, dict) and (p.get("name") == name or name in _as_list(p.get("aliases"))):
                hits.append((org, p, doc))
    if not hits:
        raise ToolsError(f"no position {name} in the listed registries")
    if len(hits) > 1:
        raise ToolsError(f"{name} is in several organizations: {', '.join(h[0] for h in hits)}")
    return hits[0]


def profile(position, doc):
    """The tool ids a position may use, in catalogue order: the catalogue's `default: true` tools, its unit's
    `tools:`, then its own `tools:`; an entry "-id" removes one given earlier."""
    cat = doc.get("tools") or {}
    want = [t for t, e in cat.items() if isinstance(e, dict) and e.get("default")]
    unit = (doc.get("units") or {}).get(position.get("unit")) if isinstance(doc.get("units"), dict) else None
    layers = [_as_list((unit or {}).get("tools")) if isinstance(unit, dict) else [], _as_list(position.get("tools"))]
    for layer in layers:
        for t in layer:
            if t.startswith("-"):
                want = [w for w in want if w != t[1:]]
            elif t not in want:
                want.append(t)
    order = list(cat)
    return sorted(want, key=lambda t: order.index(t) if t in order else len(order))


HOME_VAR = re.compile(r"^(\$\{HOME\}|\$HOME)(?=/|$)")


def _expand_home(v):
    """A leading ~ or ${HOME}/$HOME as the user's home; anything else unchanged."""
    v = HOME_VAR.sub("~", str(v))
    return os.path.expanduser(v) if v == "~" or v.startswith("~/") else v


def _names_path(arg, pattern):
    """Whether a command argument or an env value names a credential path: the path itself, or a file or folder
    under a glob's literal stem (`~/.config/acme/x/**` is named by `~/.config/acme/x`). `~`, `${HOME}` and `$HOME`
    are the same home."""
    a = _expand_home(arg).rstrip("/")
    p = os.path.expanduser(str(pattern))
    stem = re.split(r"[*?[]", p, maxsplit=1)[0].rstrip("/")
    return a == p.rstrip("/") or (stem != p.rstrip("/") and (a == stem or a.startswith(stem + "/")))


def _has_key(v, key):
    if isinstance(v, dict):
        return key in v or any(_has_key(x, key) for x in v.values())
    return isinstance(v, list) and any(_has_key(x, key) for x in v)


MODES = ("add", "enforce")


def check(docs):
    """Problems in every registry's `tools:` catalogue and the positions' lists, as strings."""
    out = []
    ports = {}
    for org, doc in docs.items():
        cat = doc.get("tools")
        names = {p.get("name") for p in doc.get("positions") or [] if isinstance(p, dict)}
        creds = (doc.get("credentials") or {}) if isinstance(doc.get("credentials"), dict) else {}
        if cat is not None and not isinstance(cat, dict):
            out.append(f"{org}: tools: is not a mapping of tool ids")
            cat = {}
        cat = cat or {}
        if doc.get("tools_mode", "add") not in MODES:
            out.append(f"{org}: tools_mode is add or enforce")
        ex = doc.get("tools_excluded")
        if ex is not None and not isinstance(ex, list):
            out.append(f"{org}: tools_excluded is a list of {{plugin, basis}}")
            ex = []
        for i, x in enumerate(ex or []):
            if not isinstance(x, dict) or not (x.get("plugin") or x.get("url")) or not x.get("basis"):
                out.append(f"{org}: tools_excluded[{i}] needs plugin or url, and basis (the owner's ask id or a source)")
        banned = excluded_plugins(doc)
        for tid, e in cat.items():
            if isinstance(e, dict) and e.get("kind") == "plugin" and e.get("plugin") in banned:
                out.append(f"{org}: tools.{tid}: plugin {e['plugin']} is in tools_excluded")
        for tid, e in cat.items():
            where = f"{org}: tools.{tid}"
            if not ID_RE.match(str(tid)):
                out.append(f"{where}: an id is lowercase letters, digits, - or _, at most 48 characters")
            if not isinstance(e, dict):
                out.append(f"{where}: not a mapping")
                continue
            kind = e.get("kind")
            if kind not in KINDS:
                out.append(f"{where}: kind is one of {', '.join(KINDS)}")
            for k in ("acts_as", "keeper", "scope"):
                if not e.get(k):
                    out.append(f"{where}: needs {k}")
            if e.get("keeper") and e["keeper"] not in names:
                out.append(f"{where}: keeper {e['keeper']} is not a position of {org}")
            for s in _as_list(e.get("scope")):
                if s not in SCOPES:
                    out.append(f"{where}: scope {s} is one of {', '.join(SCOPES)}")
            if kind == "mcp-stdio" and not (isinstance(e.get("command"), list) and e["command"]):
                out.append(f"{where}: an mcp-stdio tool needs command: [program, args…]")
            if e.get("shared") is not None:
                sh = e["shared"]
                if kind != "mcp-stdio" or not isinstance(sh, dict):
                    out.append(f"{where}: shared is {{port, args, path, token_env}} on an mcp-stdio tool")
                elif not isinstance(sh.get("port"), int) or not 1024 < sh["port"] < 65536:
                    out.append(f"{where}: shared.port is a free TCP port above 1024")
                else:
                    if sh["port"] in ports:
                        out.append(f"{where}: shared.port {sh['port']} is also {ports[sh['port']]}'s")
                    ports.setdefault(sh["port"], f"{org}/{tid}")
                    if not sh.get("token_env"):
                        out.append(f"{where}: shared.token_env names the variable the server takes its access token "
                                   f"from; a shared server without one is open to every process on the machine")
                    if not isinstance(sh.get("args") or [], list):
                        out.append(f"{where}: shared.args is a list; {{port}} in it is replaced by the port")
            if kind in ("mcp-remote", "connector") and not e.get("url"):
                out.append(f"{where}: a {kind} tool needs url")
            if kind == "plugin" and not e.get("plugin"):
                out.append(f"{where}: a plugin tool needs plugin: <name>@<marketplace>")
            if kind == "connector" and not e.get("account_bound", True):
                out.append(f"{where}: a connector is always account_bound")
            if kind == "mcp-remote" and e.get("oauth") and not e.get("account_bound"):
                out.append(f"{where}: an OAuth sign-in stays with one account: mark it account_bound")
            if _has_key(e, "vault"):
                out.append(f"{where}: a tool names its credential by id (credential:), never a vault reference")
            c = e.get("credential")
            if c is not None:
                cid = c.get("id") if isinstance(c, dict) else None
                if not cid:
                    out.append(f"{where}: credential is {{id: <credential id>, env: NAME | file: NAME}}")
                elif cid not in creds:
                    out.append(f"{where}: credential {cid} is not in {org}'s credentials catalogue")
                ent = creds.get(cid) or {} if cid else {}
                if isinstance(c, dict) and ent.get("ref") and not (c.get("env") or c.get("file")) \
                        and kind == "mcp-stdio":
                    out.append(f"{where}: say how the server takes the credential: env: NAME or file: NAME")
                env = e.get("env") if isinstance(e.get("env"), dict) else {}
                named = list(_as_list(e.get("command"))) + [str(v) for v in env.values()]
                if cid in creds and not ent.get("ref") and kind == "mcp-stdio" and not any(
                        _names_path(x, pth) for x in named for pth in ent.get("paths") or []):
                    out.append(f"{where}: credential {cid} is a file: the command or an env value names its path")
                if isinstance(c, dict) and kind in ("mcp-remote",):
                    out.append(f"{where}: a header credential for a remote server is not built yet (docs/tools.md §5)")
            for field in ("env", "headers"):
                if e.get(field) is not None and not isinstance(e.get(field), dict):
                    out.append(f"{where}: {field} is a mapping NAME: value")
                for k, v in (e.get(field) or {}).items() if isinstance(e.get(field), dict) else []:
                    if field == "env" and not ENV_NAME_RE.match(str(k)):
                        out.append(f"{where}: env name {k} is not a variable name")
                    if SECRETISH.search(str(k)) and not REF_LIKE.match(str(v)):
                        out.append(f"{where}: {field}.{k} looks like a secret written as a value; name a credential")
        for p in doc.get("positions") or []:
            if not isinstance(p, dict):
                continue
            for t in _as_list(p.get("tools")):
                if t.lstrip("-") not in cat:
                    out.append(f"{org}: position {p.get('name')}: tool {t.lstrip('-')} is not in the catalogue")
        units = doc.get("units") if isinstance(doc.get("units"), dict) else {}
        for u, ue in units.items():
            for t in _as_list((ue or {}).get("tools") if isinstance(ue, dict) else None):
                if t.lstrip("-") not in cat:
                    out.append(f"{org}: unit {u}: tool {t.lstrip('-')} is not in the catalogue")
    return out


# ---------- the session config ----------

def of_bin():
    return os.path.join(HERE, "of")


def installed_plugins(config_dir):
    """Plugin ids installed in a Claude config folder (names only)."""
    try:
        with open(os.path.join(os.path.expanduser(config_dir), "plugins", "installed_plugins.json")) as fh:
            return sorted((json.load(fh).get("plugins") or {}).keys())
    except (OSError, ValueError):
        return []


# ---------- shared servers (docs/tools.md §4a) ----------

def _shared(e):
    """The entry's `shared:` settings, or None: one server for every session on the machine (2026-11-14, of_architect's
    hand-over 75265244: 79 Grafana servers for 78 sessions while the laptop's swap was full)."""
    sh = e.get("shared") if isinstance(e, dict) and e.get("kind") == "mcp-stdio" else None
    # a shared server without an access token would be open to every process on the machine (of_review, PR 132)
    return sh if isinstance(sh, dict) and isinstance(sh.get("port"), int) and sh.get("token_env") else None


def shared_label(org, tid):
    return f"dev.organic-framework.tool.{org}.{tid}"


def shared_token(org, tid, create=False, state_dir=None):
    """The shared server's access token: OF's own random value, never an outside credential, in a mode-600 file."""
    d = os.path.join(state_dir or STATE_DIR, "tools", "shared")
    path = os.path.join(d, f"{org}.{tid}.token")
    try:
        with open(path) as fh:
            return fh.read().strip()
    except FileNotFoundError:
        if not create:
            raise ToolsError(f"no access token for the shared server {org}/{tid}: it was never started")
    os.makedirs(d, mode=0o700, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    try:
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fh:
            fh.write(tok)
    except FileExistsError:
        return shared_token(org, tid, state_dir=state_dir)
    return tok


def port_up(port, timeout=0.3):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def stdio_command(e, doc, ofb):
    """The server's command line: a credential kept in the vault is given by the broker to the server's process
    alone; one still kept in a file is read by the server itself."""
    cmd = [os.path.expanduser(str(x)) for x in e["command"]]
    c = e.get("credential") if isinstance(e.get("credential"), dict) else None
    creds = doc.get("credentials") if isinstance(doc.get("credentials"), dict) else {}
    if c and c.get("id") and (creds.get(c["id"]) or {}).get("ref"):
        how = ["--file", c["file"]] if c.get("file") else ["--env", c.get("env") or c["id"].upper()]
        cmd = [ofb, "secret", "run", c["id"], *how, "--", *cmd]
    return cmd


def serve_command(org, tid, doc, ofb=None, state_dir=None):
    """(argv, env) of the one shared server of tool `tid`, for its launchd unit (`of tools serve org/tid`)."""
    e = (doc.get("tools") or {}).get(tid)
    sh = _shared(e)
    if not sh:
        raise ToolsError(f"{org}/{tid} is not a shared tool (shared: {{port: …}} on an mcp-stdio entry)")
    argv = stdio_command(e, doc, ofb or of_bin()) + [str(a).replace("{port}", str(sh["port"]))
                                                      for a in sh.get("args") or []]
    env = dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK="")
    env.update({str(k): _expand_home(v) for k, v in (e.get("env") or {}).items()})
    if sh.get("token_env"):
        env[str(sh["token_env"])] = shared_token(org, tid, create=True, state_dir=state_dir)
    return argv, env


def _ancestors(pid=None):
    """[(pid, argv string)] of this process's parents, nearest first (ps; names and arguments only)."""
    import subprocess
    out, pid = [], pid or os.getppid()
    for _ in range(8):
        r = subprocess.run(["ps", "-o", "ppid=", "-o", "args=", "-p", str(pid)], capture_output=True, text=True)
        line = r.stdout.strip()
        if r.returncode or not line:
            break
        ppid, _, args = line.partition(" ")
        out.append((pid, args.strip()))
        if not ppid.strip().isdigit() or int(ppid) <= 1:
            break
        pid = int(ppid)
    return out


def header(org, tid, docs, ancestors=None, state_dir=None):
    """The headers a session's MCP client sends to the shared server (Claude Code's headersHelper).

    The token goes only to a Claude Code process that OF launched with this tool: the nearest parent started with
    `--mcp-config <state>/tools/<session>.mcp.json` and `--session-id <session>` (or `--resume <session>`), whose file names this tool's shared
    URL. A position is never taken from the caller's word (of_review, PR 132: a named position handed the token to
    any caller). An agent's own shell command reaches its own session's file, so it gets the token only when its
    profile has the tool."""
    doc = docs.get(org) or {}
    sh = _shared((doc.get("tools") or {}).get(tid))
    if not sh:
        raise ToolsError(f"{org}/{tid} is not a shared tool")
    want = f"http://127.0.0.1:{sh['port']}{sh.get('path') or '/mcp'}"
    tools_dir = os.path.realpath(os.path.join(state_dir or STATE_DIR, "tools"))
    for _pid, args in (ancestors if ancestors is not None else _ancestors()):
        argv = args.split()
        if "--mcp-config" not in argv:
            continue
        path = argv[argv.index("--mcp-config") + 1] if argv.index("--mcp-config") + 1 < len(argv) else ""
        # A move, a restart or `of resume-command` relaunches the session with `--resume <session>` (2026-11-14, a user
        # organization: moved sessions got no token and Claude Code fell back to OAuth); the file check is the same.
        flag = next((f for f in ("--session-id", "--resume") if f in argv[:-1]), None)
        sid = argv[argv.index(flag) + 1] if flag else None
        if not sid or os.path.realpath(path) != os.path.join(tools_dir, f"{sid}.mcp.json"):
            raise ToolsError("the calling Claude Code session was not launched by OF with its tool profile")
        try:
            with open(path) as fh:
                entry = (json.load(fh).get("mcpServers") or {}).get(tid) or {}
        except (OSError, ValueError):
            entry = {}
        if entry.get("url") != want:
            raise ToolsError(f"{tid} is not in the calling session's tool profile")
        return {"Authorization": f"Bearer {shared_token(org, tid, state_dir=state_dir)}"}
    raise ToolsError("no calling Claude Code session launched by OF")


def shared_units(docs, python=None, ofb=None, state_dir=None):
    """{label: plist text} for every shared tool of every listed organization."""
    import of_server
    python, ofb = python or sys.executable, ofb or of_bin()
    out = {}
    for org, doc in docs.items():
        for tid, e in (doc.get("tools") or {}).items() if isinstance(doc.get("tools"), dict) else []:
            if _shared(e):
                log = os.path.join(state_dir or STATE_DIR, "tools", "shared", f"{org}.{tid}.log")
                out[shared_label(org, tid)] = of_server._plist(
                    shared_label(org, tid), [python, ofb, "tools", "serve", f"{org}/{tid}"], log,
                    "  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><true/>\n"
                    "  <key>ThrottleInterval</key><integer>10</integer>\n")
    return out


def install_shared(docs, uninstall=False, run=None):
    import subprocess
    run = run or subprocess.run
    domain, out = f"gui/{os.getuid()}", []
    os.makedirs(os.path.join(STATE_DIR, "tools", "shared"), mode=0o700, exist_ok=True)
    for label, text in shared_units(docs).items():
        path = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
        if uninstall:
            if os.path.exists(path):
                os.remove(path)
            out.append(f"removed {label}")
            continue
        with open(path + ".tmp", "w") as fh:
            fh.write(text)
        os.replace(path + ".tmp", path)
        r = run(["launchctl", "bootstrap", domain, path], capture_output=True)
        out.append(f"{label}: {'loaded' if r.returncode == 0 else 'not loaded (launchctl ' + str(r.returncode) + ')'}")
    return out


def excluded_plugins(doc):
    """Plugin ids the organization never lets a position start with, whatever its account enables
    (`tools_excluded`, each with its basis)."""
    ex = doc.get("tools_excluded")
    return {str(x["plugin"]) for x in ex if isinstance(x, dict) and x.get("plugin")} if isinstance(ex, list) else set()


def excluded_urls(doc):
    """MCP server URLs (a trailing * matches any path) no position's session may load: a claude.ai connector or a
    plugin's server, whatever the account has connected (`tools_excluded` entries with url)."""
    ex = doc.get("tools_excluded")
    return sorted({str(x["url"]) for x in ex if isinstance(x, dict) and x.get("url")}) if isinstance(ex, list) else []


def session_config(position, doc, config_dir=None, ofb=None, installed=None, org=None, up=port_up):
    """{"mcp": the --mcp-config JSON or None, "settings": the --settings keys, "account_bound": [ids],
    "mode": add|enforce, "notes": [str]} for this position on this account.

    `tools_mode: enforce` in the registry makes the session see exactly its profile. Until its keeper sets it (the
    default, `add`), the profile only adds its portable servers and the account's own tools stay: a catalogue that
    is still being filled must not strip a session (2026-11-12: one entry made every position of an organization
    strict, and two moved keepers lost the Cloudflare and Grafana tools their work needs)."""
    cat = doc.get("tools") or {}
    mode = doc.get("tools_mode") if doc.get("tools_mode") in MODES else "add"
    ids = profile(position, doc)
    ofb = ofb or of_bin()
    servers, allowed, plugins_on, bound, notes = {}, [], set(), [], []
    banned = excluded_plugins(doc)
    for tid in ids:
        e = cat.get(tid)
        if not isinstance(e, dict):
            notes.append(f"{tid}: not in the catalogue; skipped")
            continue
        kind = e.get("kind")
        sh = _shared(e) if org else None
        if sh and up(sh["port"]):  # the machine's one server; the helper gives the token to this profile only
            s = {"type": "http", "url": f"http://127.0.0.1:{sh['port']}{sh.get('path') or '/mcp'}"}
            if sh.get("token_env"):
                s["headersHelper"] = " ".join(shlex.quote(x) for x in (ofb, "tools", "header", f"{org}/{tid}"))
            servers[tid] = s
            allowed.append({"serverName": tid})
        elif kind == "mcp-stdio":
            if sh:
                notes.append(f"{tid}: the shared server on port {sh['port']} is not running; this session starts "
                             f"its own (of tools shared install)")
            cmd = stdio_command(e, doc, ofb)
            s = {"type": "stdio", "command": cmd[0], "args": cmd[1:]}
            if e.get("env"):
                # a leading ~, ${HOME} or $HOME expanded here, as in the command: Claude Code expands only the braced
                # ${VAR} form (docs/tools.md §4), and the check accepts all three
                s["env"] = {str(k): _expand_home(v) for k, v in e["env"].items()}
            servers[tid] = s
            allowed.append({"serverName": tid})
        elif kind == "mcp-remote" and not e.get("account_bound"):
            s = {"type": e.get("transport") or "http", "url": e["url"]}
            if e.get("headers"):
                s["headers"] = {str(k): str(v) for k, v in e["headers"].items()}
            servers[tid] = s
            allowed.append({"serverName": tid})
        elif kind in ("mcp-remote", "connector"):  # account-bound: the account's own definition and sign-in
            allowed.append({"serverUrl": e["url"]})
            bound.append(tid)
        elif kind == "plugin":
            if e["plugin"] in banned:
                notes.append(f"{tid}: plugin {e['plugin']} is in tools_excluded; skipped")
                continue
            plugins_on.add(e["plugin"])
            if e.get("url"):
                allowed.append({"serverUrl": e["url"]})
            if e.get("account_bound"):
                bound.append(tid)
    installed = installed_plugins(config_dir) if installed is None and config_dir else (installed or [])
    plugins = {p: p in plugins_on for p in sorted(set(installed) | plugins_on)}
    for p in sorted(plugins_on - set(installed)):
        notes.append(f"plugin {p} is not installed on this account; the session starts without it")
    settings = {"allowedMcpServers": allowed}
    if plugins:
        settings["enabledPlugins"] = plugins
    if mode == "add":
        # enabledPlugins in --settings merges per entry (docs/tools.md §4), so naming only the excluded plugins
        # turns them off and leaves the account's other plugins as they are (2026-11-14, a user organization:
        # no agent acts as its owner in Slack, while one account still enables the Slack plugin)
        settings = {"enabledPlugins": {p: False for p in sorted(banned)}} if banned else {}
        notes.append("tools_mode add: the profile's portable servers are added and the account's own tools stay; "
                     "the keeper sets tools_mode: enforce once the catalogue is complete")
    elif banned:
        settings["enabledPlugins"] = dict(plugins, **{p: False for p in banned})
    if excluded_urls(doc):
        # deniedMcpServers in a session's --settings hides a claude.ai connector and a plugin's server by URL
        # (probed 2026-11-14, Claude Code 2.1.325: a connected claude.ai Slack connector was gone from the session)
        settings["deniedMcpServers"] = [{"serverUrl": u} for u in excluded_urls(doc)]
    return {"position": position.get("name"), "tools": ids, "mcp": {"mcpServers": servers} if servers else None,
            "settings": settings, "account_bound": bound, "mode": mode, "notes": notes}


def write_mcp_config(cfg, sid, state_dir=None):
    """Write the --mcp-config JSON (mode 600; names, paths and placeholders only) and return its path."""
    if not cfg.get("mcp"):
        return None
    d = os.path.join(state_dir or STATE_DIR, "tools")
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, f"{sid}.mcp.json")
    tmp = f"{path}.{os.getpid()}"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(cfg["mcp"], fh, indent=1)
    os.replace(tmp, path)
    return path


def launch_parts(position_name, sid, config_dir, orgs_file=None, state_dir=None):
    """For a launcher (of spawn, of capacity move/resume): (extra argv, settings keys to merge into the session's
    one --settings file). A position whose organization has neither a `tools:` catalogue nor `tools_excluded`
    gets ([], {}): nothing changes until its structure keeper declares one. `tools_excluded` alone applies (of_review,
    organic-framework PR 121: an organization excluding a plugin before it has a catalogue)."""
    docs = registries(orgs_file)
    org, p, doc = find_position(position_name, docs)
    if not doc.get("tools") and not excluded_plugins(doc) and not excluded_urls(doc):
        return [], {}
    cfg = session_config(p, doc, config_dir, org=org)
    path = write_mcp_config(cfg, sid, state_dir)
    return (["--mcp-config", path] if path else []), cfg["settings"]


# ---------- the inventory ----------

def config_dirs(home=None):
    home = home or os.path.expanduser("~")
    out = []
    for n in sorted(os.listdir(home)):
        d = os.path.join(home, n)
        if n.startswith(".claude") and os.path.isdir(d) and os.path.exists(os.path.join(d, "settings.json")):
            out.append(d)
    return out


def _load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _server_shape(s):
    """A server entry by names only: kind, program or host, env and header names."""
    if not isinstance(s, dict):
        return {}
    url = s.get("url") or ""
    kind = s.get("type") or ("http" if url else "stdio")
    out = {"kind": kind}
    if url:  # the host only: some services put a key in the path or the query (2026-11-11: an MCP URL with a key)
        m = re.match(r"^([a-z]+://[^/?#]+)(.*)$", str(url))
        out["url"] = (m.group(1) + ("/…" if m.group(2) not in ("", "/") else "")) if m else "(not a URL)"
        if m and re.search(r"[0-9a-f]{24,}|[A-Za-z0-9_-]{32,}", m.group(2)):
            out["url_key"] = True  # a long opaque segment: a credential in the URL
    if s.get("command"):
        out["program"] = os.path.basename(str(s["command"]))
    if s.get("env"):
        out["env"] = sorted(s["env"])
    if s.get("headers"):
        out["headers"] = sorted(s["headers"])
    if s.get("oauth"):
        out["oauth"] = True
    return out


def plugin_servers(config_dir, plugin_id):
    """MCP servers a plugin brings (from its newest cached copy), by names only."""
    name, _, market = plugin_id.partition("@")
    base = os.path.join(config_dir, "plugins", "cache", market, name)
    try:
        versions = sorted(os.listdir(base), key=lambda v: os.path.getmtime(os.path.join(base, v)))
    except OSError:
        return {}
    for v in reversed(versions):
        j = _load_json(os.path.join(base, v, ".mcp.json"))
        if j:
            j = j.get("mcpServers", j)
            return {k: _server_shape(s) for k, s in j.items()}
    return {}


def inventory_dir(d, default_dir=None):
    default_dir = default_dir or os.path.expanduser("~/.claude")
    settings = _load_json(os.path.join(d, "settings.json"))
    cj = os.path.expanduser("~/.claude.json") if os.path.realpath(d) == os.path.realpath(default_dir) \
        else os.path.join(d, ".claude.json")
    c = _load_json(cj)
    enabled = sorted(k for k, v in (settings.get("enabledPlugins") or {}).items() if v)
    return {"config_dir": d,
            "plugins_installed": installed_plugins(d),
            "plugins_enabled": enabled,
            "plugin_servers": {p: plugin_servers(d, p) for p in enabled if plugin_servers(d, p)},
            "user_servers": {k: _server_shape(s) for k, s in (c.get("mcpServers") or {}).items()},
            "local_servers": {p: sorted((v.get("mcpServers") or {}).keys())
                              for p, v in (c.get("projects") or {}).items() if v.get("mcpServers")},
            "connectors_ever": sorted(c.get("claudeAiMcpEverConnected") or [])}


def inventory_codex(path=None):
    path = path or os.path.expanduser("~/.codex/config.toml")
    try:
        import tomllib
        with open(path, "rb") as fh:
            c = tomllib.load(fh)
    except Exception:  # noqa: BLE001 - no Codex, or no tomllib
        return {}
    return {k: _server_shape(v) | ({"enabled": False} if v.get("enabled") is False else {})
            for k, v in (c.get("mcp_servers") or {}).items()}


def project_mcp_files(paths, depth=4):
    skip = {".git", "node_modules", "target", "dist", "build", ".venv", "venv", "__pycache__"}
    out = []
    for root in paths:
        root = os.path.expanduser(root)
        base = root.rstrip("/").count("/")
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in skip] if d.count("/") - base < depth else []
            if ".mcp.json" in files:
                out.append(os.path.join(d, ".mcp.json"))
    return sorted(out)


def usage(days=14, db=None):
    """{server: {position or "session xxxxxxxx": calls}} from the store's tool names, last N days."""
    import datetime
    db = db or os.environ.get("OF_DB") or os.path.join(STATE_DIR, "of.db")
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    pos = dict(conn.execute("SELECT session_id, position FROM event WHERE position IS NOT NULL AND position != '' "
                            "AND session_id IS NOT NULL GROUP BY session_id"))
    out = {}
    for sid, names in conn.execute("SELECT session_id, tool_names FROM transcript_record WHERE timestamp >= ? "
                                   "AND tool_names LIKE '%mcp__%'", (since,)):
        for t in (names or "").split(","):
            if not t.startswith("mcp__"):
                continue
            server = t.split("__")[1]
            who = pos.get(sid) or f"session {str(sid)[:8]}"
            out.setdefault(server, {})
            out[server][who] = out[server].get(who, 0) + 1
    return out


def inventory(days=14, orgs_file=None):
    import of_credentials
    paths = []
    try:
        import yaml
        with open(of_credentials._orgs_file(orgs_file)) as fh:
            for o in (yaml.safe_load(fh) or {}).get("orgs") or []:
                paths += _as_list(o.get("paths"))
    except Exception:  # noqa: BLE001
        pass
    return {"config_dirs": [inventory_dir(d) for d in config_dirs()], "codex": inventory_codex(),
            "project_mcp_json": project_mcp_files(paths), "usage": usage(days)}


def print_inventory(inv, days):
    home = os.path.expanduser("~")
    short = lambda p: p.replace(home, "~")  # noqa: E731
    print("Per Claude config folder (an account):")
    for r in inv["config_dirs"]:
        print(f"\n{short(r['config_dir'])}")
        print(f"  plugins enabled: {', '.join(r['plugins_enabled']) or '-'}"
              f"   (installed: {', '.join(r['plugins_installed']) or '-'})")
        for p, ss in r["plugin_servers"].items():
            for n, s in ss.items():
                print(f"  plugin server  {p} -> {n}: {s.get('kind')} {s.get('url') or s.get('program') or ''}"
                      f"{' (OAuth)' if s.get('oauth') else ''}")
        for n, s in r["user_servers"].items():
            print(f"  user server    {n}: {s.get('kind')} {s.get('url') or s.get('program') or ''}"
                  f"{'  env: ' + ', '.join(s['env']) if s.get('env') else ''}")
        for p, ns in r["local_servers"].items():
            print(f"  local server   {short(p)}: {', '.join(ns)}")
        print(f"  connectors ever connected: {', '.join(r['connectors_ever']) or '-'}")
    keyed = [f"{short(r['config_dir'])}: {n}" for r in inv["config_dirs"] for n, s in r["user_servers"].items()
             if s.get("url_key")] + [f"codex: {n}" for n, s in inv["codex"].items() if s.get("url_key")]
    print("\nCodex (~/.codex/config.toml):")
    for n, s in inv["codex"].items():
        print(f"  {n}: {s.get('kind')} {s.get('url') or s.get('program') or ''}"
              f"{'  env: ' + ', '.join(s['env']) if s.get('env') else ''}{'  (disabled)' if s.get('enabled') is False else ''}")
    if keyed:
        print("\nA credential in an MCP server's URL (shown by host only; its keeper rotates it and moves it out):")
        for k in keyed:
            print(f"  {k}")
    print(f"\nProject .mcp.json files: {', '.join(short(p) for p in inv['project_mcp_json']) or 'none'}")
    print(f"\nMCP calls in the last {days} days, by server and position:")
    for server, who in sorted(inv["usage"].items(), key=lambda kv: -sum(kv[1].values())):
        top = sorted(who.items(), key=lambda kv: -kv[1])
        print(f"  {server}: {sum(who.values())} calls, {len(who)} positions or sessions: "
              + ", ".join(f"{w} {n}" for w, n in top))


# ---------- moving a credential out of an MCP config ----------

def adopt_url(server, to, codex_config=None, force=False):
    """Write a Codex MCP server's URL (which holds its key) into a file of mode 600, never printing it; returns the
    host. The Codex entry is left as it is: its owner removes it once the position's tool works."""
    import tomllib
    with open(os.path.expanduser(codex_config or "~/.codex/config.toml"), "rb") as fh:
        url = ((tomllib.load(fh).get("mcp_servers") or {}).get(server) or {}).get("url")
    if not url:
        raise ToolsError(f"Codex has no remote MCP server {server}")
    path = os.path.expanduser(to)
    if os.path.exists(path) and not force:
        raise ToolsError(f"{to} exists; --force replaces it")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        fh.write(url)
    os.replace(tmp, path)
    return _server_shape({"url": url})["url"]


# ---------- the CLI ----------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="of tools", description="A position's tools, bound to the position (docs/tools.md).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    iv = sub.add_parser("inventory", help="read-only, names only: plugins, MCP servers, connectors and their use")
    iv.add_argument("--days", type=int, default=14)
    iv.add_argument("--json", action="store_true")
    sub.add_parser("check", help="validate the tools catalogues and the positions' tools lists")
    pl = sub.add_parser("plan", help="the session config the launcher generates for a position on an account")
    pl.add_argument("position")
    pl.add_argument("--config-dir")
    pl.add_argument("--json", action="store_true")
    ad = sub.add_parser("adopt-url", help="write a Codex MCP server's URL (with its key) into a mode-600 file, unprinted")
    ad.add_argument("server")
    ad.add_argument("--to", required=True)
    ad.add_argument("--force", action="store_true")
    sv = sub.add_parser("serve", help="run a shared tool's one server for the machine (its launchd unit runs this)")
    sv.add_argument("tool", help="<org>/<tool id>")
    hd = sub.add_parser("header", help="the headers a session sends to a shared server (Claude Code's headersHelper)")
    hd.add_argument("tool", help="<org>/<tool id>")
    hd.add_argument("--position", help=argparse.SUPPRESS)  # ignored: configs written before cbc5273 still pass it
    sd = sub.add_parser("shared", help="the shared servers' launchd units: install, uninstall or status")
    sd.add_argument("action", choices=("install", "uninstall", "status"))
    a = ap.parse_args(argv)
    try:
        if a.cmd in ("serve", "header"):
            org, _, tid = a.tool.partition("/")
            docs = registries()
            if org not in docs:
                raise ToolsError(f"no organization {org} in the listed registries")
            if a.cmd == "header":
                print(json.dumps(header(org, tid, docs)))
                return 0
            argv_, env = serve_command(org, tid, docs[org])
            os.execvpe(argv_[0], argv_, env)
        if a.cmd == "shared":
            docs = registries()
            if a.action == "status":
                for org, doc in docs.items():
                    for tid, e in (doc.get("tools") or {}).items() if isinstance(doc.get("tools"), dict) else []:
                        if _shared(e):
                            port = _shared(e)["port"]
                            print(f"{org}/{tid}: port {port} {'answers' if port_up(port) else 'does not answer'}")
                return 0
            for line in install_shared(docs, uninstall=a.action == "uninstall"):
                print(line)
            return 0
        if a.cmd == "inventory":
            inv = inventory(a.days)
            if a.json:
                print(json.dumps(inv, indent=1, sort_keys=True))
            else:
                print_inventory(inv, a.days)
            return 0
        if a.cmd == "adopt-url":
            host = adopt_url(a.server, a.to, force=a.force)
            print(f"written {a.to} (mode 600): the URL of {host}; Codex's entry is unchanged")
            return 0
        if a.cmd == "check":
            probs = check(registries())
            for p in probs:
                print(p)
            print(f"{len(probs)} problem(s)" if probs else "tools: ok")
            return 1 if probs else 0
        if a.cmd == "plan":
            docs = registries()
            org, p, doc = find_position(a.position, docs)
            cfg = session_config(p, doc, a.config_dir, org=org)
            if a.json:
                print(json.dumps(cfg, indent=1))
                return 0
            print(f"{org}:{cfg['position']} tools: {', '.join(cfg['tools']) or 'none'}"
                  + ("" if doc.get("tools") else f"  ({org} declares no tools: catalogue yet; the account's own apply)"))
            if cfg["account_bound"]:
                print(f"account-bound (need a sign-in on the account): {', '.join(cfg['account_bound'])}")
            print("--mcp-config:", json.dumps(cfg["mcp"], indent=1) if cfg["mcp"] else "none")
            print("--settings keys:", json.dumps(cfg["settings"], indent=1))
            for n in cfg["notes"]:
                print("note:", n)
            return 0
    except ToolsError as e:
        print(f"of tools: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
