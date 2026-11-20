"""The door: how a session in an operating-system sandbox reaches OF (RFC 0008 §4.5).

Inside a Codex position's sandbox, `of` neither reads the store nor holds the machine's key. When its environment names
OF_DOOR, it sends its argv and stdin over that Unix socket, which the listener serves in the session's own folder, and
prints what comes back. The listener checks the request against ALLOW as `of`'s own parser reads it, runs the command
in its own process with the caller forced to the socket's session and position, and returns its output.

of_architect's conditions (msg 01a1f9ee-5080, 2026-11-20):
1. the policy reads the arguments as `of`'s parser does, never the argv text: the subcommand on the allowlist, every
   option on its own list per subcommand, and --org forced to the caller's organization on every read;
2. no confused deputy: no allowed option makes `of` open a path the caller names; text comes in on stdin;
3. `of event show <id>` returns only the kinds a position needs to check an owner's answer or a hand-over.
of_server and of_secrets-review review the allowlist with its options before it is on."""

import argparse
import os

# Per subcommand: the actions (its first positional's choices) allowed, and the option dests allowed; any other option
# given a value other than its default refuses the request. NO_PATHS lists, per subcommand, the allowed options whose
# value is text that `of` stores or matches and never opens (condition 2), for the review.
ALLOW = {
    "status": {"actions": {"working", "waiting", "idle", "done"},
               "options": {"state", "on", "note", "answered", "done_task", "reopen_task", "work", "ask"}},
    "ask": {"actions": None,
            "options": {"question", "options", "yes", "task", "command", "where", "work", "tags", "blocking", "due",
                        "to", "amend", "reason"}},
    "asks": {"actions": {"list"}, "options": {"action", "to_me", "all", "json", "org"}, "org": True},
    "send": {"actions": None, "options": {"to", "text", "reply_to", "fyi", "urgent"}},
    "inbox": {"actions": {"list"}, "options": {"action", "mine", "take", "taken"}, "require": {"mine": True}},
    "handover": {"actions": {"list", "propose", "accept", "decline", "block", "deliver", "verify", "reject", "close",
                             "return", "withdraw", "note"},
                 "options": {"action", "ref", "title", "done_when", "basis", "verify", "due", "blocking", "mandate",
                             "context", "systems", "paths", "note", "result", "on", "close", "all", "json", "org"},
                 "org": {"list", None}},
    "loop": {"actions": {"hypothesis", "run", "decision", "show", "locate", "wait"},
             "options": {"action", "work", "text", "predict", "cost", "voi", "rung", "hypothesis", "result",
                         "evidence", "money", "to_money", "metric", "value", "proxy_held", "proxy_for", "valid_when",
                         "observed_at", "direct", "accepted", "runner_h", "tokens", "queued_at", "started_at",
                         "ended_at", "run", "note", "on", "ref", "until", "org"},
             "org": True},
    "prompts": {"actions": None, "options": {"mine", "last"}, "require": {"mine": True}},
    "who": {"actions": None, "options": {"json", "long", "org"}, "org": True},
    "event": {"actions": {"show"}, "action_dest": "kind", "options": {"kind", "id", "org"}, "org": True},
}
NO_PATHS = {
    "status": ("note",), "ask": ("question", "options", "task", "command", "reason", "tags"),
    "send": ("text",), "handover": ("title", "done_when", "basis", "context", "paths", "note", "result", "mandate"),
    "loop": ("text", "result", "evidence", "note", "ref", "source"),
}
# options that name another caller, a machine-wide view or a path: never through the door, whatever the subcommand
NEVER = {"sender", "session", "pane", "prompt", "panel", "watch", "classic", "chat", "source"}
MAX_REQUEST = 1 << 20      # one request: argv and stdin together
MAX_OUTPUT = 4 << 20       # what the door returns of one command's stdout and stderr


class Refused(Exception):
    """A request the door does not run; the message says why, for the caller's stderr."""


class _Quiet(argparse.ArgumentParser):
    def error(self, message):
        raise Refused(f"of: {message}")


def _parser():
    import importlib.machinery
    import importlib.util
    here = os.path.dirname(os.path.realpath(__file__))
    loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(here, "of"))
    spec = importlib.util.spec_from_loader("of_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod.build_parser()


def _quiet(ap):
    """The parser with errors raised, never printed with an exit, its subparsers too."""
    ap.__class__ = _Quiet
    for a in ap._actions:
        if isinstance(a, argparse._SubParsersAction):
            for sp in a.choices.values():
                _quiet(sp)
    return ap


def _sub(ap, cmd):
    subs = next(a for a in ap._actions if isinstance(a, argparse._SubParsersAction))
    return subs.choices[cmd]


def check(argv, org, parser=None):
    """(argv to run, subcommand) for a request the policy allows, with --org forced to ORG where the subcommand
    reads across positions; else Refused. ARGV is read as `of`'s parser reads it."""
    argv = [str(a) for a in argv]
    if not argv or argv[0] not in ALLOW:
        raise Refused(f"of: `{argv[0] if argv else ''}` does not run inside a sandboxed session; it reaches OF only "
                      "through its door, which serves " + ", ".join(sorted(ALLOW)) + " (RFC 0008 §4.5)")
    if any(a in ("-h", "--help") for a in argv):
        return argv, argv[0]
    ap = _quiet(parser or _parser())
    args = ap.parse_args(argv)
    cmd = args.cmd
    rule = ALLOW[cmd]
    sp = _sub(ap, cmd)
    defaults = {a.dest: a.default for a in sp._actions}
    shown = {a.dest: (a.option_strings[-1] if a.option_strings else a.dest) for a in sp._actions}
    for dest, value in vars(args).items():
        if dest in ("cmd", "fn") or dest not in defaults:
            continue
        if value == defaults[dest] or (value in ([], None, False) and defaults[dest] in ([], None, False)):
            continue
        if dest in NEVER or dest not in rule["options"] | {"action", "state", rule.get("action_dest") or "action"}:
            raise Refused(f"of {cmd}: `{shown.get(dest, dest)}` is not served through the door (RFC 0008 §4.5)")
    adest = rule.get("action_dest") or ("action" if "action" in defaults else "state")
    action = getattr(args, adest, None)
    if rule["actions"] is not None and action not in rule["actions"] and not (action is None and cmd != "status"):
        raise Refused(f"of {cmd} {action}: not served through the door; it serves "
                      + ", ".join(sorted(rule["actions"])) + " (RFC 0008 §4.5)")
    for dest, want in (rule.get("require") or {}).items():
        if getattr(args, dest, None) != want:
            raise Refused(f"of {cmd}: through the door only with --{dest.replace('_', '-')} (the caller's own)")
    if rule.get("org") is True or (isinstance(rule.get("org"), set) and action in rule["org"]):
        if "org" not in defaults:
            raise Refused(f"of {cmd}: has no --org yet, so it cannot be limited to the caller's organization")
        argv = _set_org(argv, org)
    return argv, cmd


def _set_org(argv, org):
    """ARGV with every --org dropped and `--org ORG` put after the subcommand."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a == "--org":
            skip = True
            continue
        if a.startswith("--org="):
            continue
        out.append(a)
    return out[:1] + ["--org", str(org)] + out[1:]


# ---------------------------------------------------------------- the wire: length-prefixed JSON, one request a socket

def _send(sock, obj):
    import json
    import struct
    data = json.dumps(obj, ensure_ascii=False).encode()
    sock.sendall(struct.pack(">I", len(data)) + data)


def _recv(sock, limit):
    import json
    import struct
    head = b""
    while len(head) < 4:
        part = sock.recv(4 - len(head))
        if not part:
            raise Refused("of: the door closed before an answer")
        head += part
    n = struct.unpack(">I", head)[0]
    if n > limit:
        raise Refused("of: the request is larger than the door takes")
    buf = bytearray()
    while len(buf) < n:
        part = sock.recv(min(65536, n - len(buf)))
        if not part:
            raise Refused("of: the door closed mid-message")
        buf += part
    return json.loads(buf.decode())


def forward(door, argv, stdin=None, timeout=180):
    """The sandboxed side: send ARGV and stdin to the listener through DOOR, print what comes back, return its code."""
    import socket
    import sys
    if stdin is None:
        stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read(MAX_REQUEST)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(door)
        _send(s, {"argv": list(argv), "stdin": stdin})
        out = _recv(s, MAX_OUTPUT * 2)
    except (OSError, Refused, ValueError) as e:
        print(f"of: OF's door at {door} did not answer ({e}); the listener runs it (of listen status)", file=sys.stderr)
        return 2
    finally:
        s.close()
    sys.stdout.write(out.get("stdout") or "")
    sys.stderr.write(out.get("stderr") or "")
    return int(out.get("code") or 0)


# ---------------------------------------------------------------- the listener's side

DOOR_FILE = "door.json"       # written by of spawn's Codex launch: {position, org, cwd, launch}
CODEX_FILE = "codex.json"     # written by the Codex session's SessionStart hook: {session, pid}
SOCKET = "of.sock"


def sessions_dir():
    """The doors' root, beside OF's state folder, never in it: a session's profile denies the state folder whole, and
    a folder inside a denied one cannot be reached (tried on the agent host, 2026-11-20: connect gave EACCES). Each
    session's own folder here is the only part of it a session sees."""
    import of_events
    return os.path.join(os.path.dirname(os.path.abspath(of_events.STATE_DIR)), "of-doors")


def _ancestors(pid):
    """PID and its ancestors, nearest first (Linux /proc, else ps)."""
    import subprocess
    out, seen = [], set()
    while pid and pid > 1 and pid not in seen:
        seen.add(pid)
        out.append(pid)
        try:
            with open(f"/proc/{pid}/stat") as fh:
                pid = int(fh.read().rsplit(")", 1)[1].split()[1])
            continue
        except OSError:
            pass
        try:
            pid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True,
                                     timeout=5).stdout.strip() or 0)
        except (OSError, ValueError, subprocess.SubprocessError):
            break
    return out


def _cmdline(pid):
    import subprocess
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return [a.decode(errors="replace") for a in fh.read().split(b"\0") if a]
    except OSError:
        pass
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return []


def codex_pid(launch, start_pid):
    """The Codex process of LAUNCH among START_PID's ancestors: the one started with its layer (-p of-<launch>)."""
    for pid in _ancestors(start_pid):
        cl = _cmdline(pid)
        if cl and os.path.basename(cl[0]).startswith("codex") and f"of-{launch}" in cl:
            return pid
    return None


def peer_pid(sock):
    """The connecting process's pid: SO_PEERCRED on Linux, LOCAL_PEERPID on macOS; None when unknown."""
    import socket
    import struct
    try:
        if hasattr(socket, "SO_PEERCRED"):
            return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
        return struct.unpack("i", sock.getsockopt(0, 2, 4))[0]   # SOL_LOCAL, LOCAL_PEERPID
    except OSError:
        return None


def run(argv, caller, stdin="", of_bin=None, timeout=120):
    """Run one allowed `of` command as CALLER ({session, position, cwd}) in this process's environment, with the
    caller forced: {stdout, stderr, code}."""
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "LANG", "LC_ALL", "USER", "LOGNAME", "TZ",
                                                         "OF_ORGS", "OF_STATE", "OF_AUTOMATION")}
    env.update({"CLAUDE_CODE_SESSION_ID": str(caller["session"]), "OF_POSITION": str(caller["position"]),
                "OF_DOOR_CALLER": "1"})
    exe = of_bin or os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
    cwd = caller.get("cwd") if caller.get("cwd") and os.path.isdir(caller["cwd"]) else os.path.expanduser("~")
    try:
        r = subprocess.run([sys.executable, exe] + list(argv), input=stdin or "", capture_output=True, text=True,
                           env=env, cwd=cwd, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"of: the command ran past {timeout} s at the door\n", "code": 2}
    return {"stdout": r.stdout[:MAX_OUTPUT], "stderr": r.stderr[:MAX_OUTPUT], "code": r.returncode}


def serve_one(conn, door, parser=None, runner=run):
    """One request on CONN for the session DOOR describes ({position, org, cwd, launch, session, pid}): the peer
    must run under that session's Codex process, the request must pass check(); then runner's answer."""
    try:
        pid = peer_pid(conn)
        if pid is None or int(door.get("pid") or 0) not in _ancestors(pid):
            raise Refused("of: this door serves only the commands of its own Codex session")
        req = _recv(conn, MAX_REQUEST)
        argv, _ = check(req.get("argv") or [], door["org"], parser)
        out = runner(argv, door, req.get("stdin") or "")
    except Refused as e:
        out = {"stdout": "", "stderr": f"{e}\n", "code": 2}
    except (OSError, ValueError, KeyError) as e:
        out = {"stdout": "", "stderr": f"of: the door failed ({type(e).__name__})\n", "code": 2}
    try:
        _send(conn, out)
    except OSError:
        pass
    finally:
        conn.close()
    return out


def read_doors(base=None):
    """{launch: door} for each session folder holding both files, with the Codex pid still running."""
    import json
    out = {}
    base = base or sessions_dir()
    try:
        names = os.listdir(base)
    except OSError:
        return out
    for launch in names:
        d = os.path.join(base, launch)
        try:
            with open(os.path.join(d, DOOR_FILE)) as fh:
                door = json.load(fh)
            with open(os.path.join(d, CODEX_FILE)) as fh:
                door.update(json.load(fh))
        except (OSError, ValueError):
            continue
        if door.get("launch") != launch or not door.get("position") or not door.get("org") or not door.get("session"):
            continue
        try:
            os.kill(int(door.get("pid")), 0)
        except (OSError, TypeError, ValueError):
            continue
        cl = _cmdline(int(door["pid"]))
        if not (cl and os.path.basename(cl[0]).startswith("codex") and f"of-{launch}" in cl):
            continue        # the pid is not that launch's Codex (reused, or written by someone else)
        door["socket"] = os.path.join(d, SOCKET)
        out[launch] = door
    return out


class Doors:
    """The listener's doors: one socket per running Codex session, opened and closed as read_doors() finds them."""

    def __init__(self, base=None, log=print):
        self.base, self.log, self.open = base, log, {}

    def tick(self):
        import socket
        import threading
        want = read_doors(self.base)
        for launch in list(self.open):
            if launch not in want:
                self.close(launch)
        for launch, door in want.items():
            if launch in self.open:
                continue
            try:
                if os.path.lexists(door["socket"]):
                    os.unlink(door["socket"])
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.bind(door["socket"])
                os.chmod(door["socket"], 0o600)
                s.listen(8)
                s.settimeout(1.0)
            except OSError as e:
                self.log(f"door {launch}: cannot open ({e})")
                continue
            stop = threading.Event()
            t = threading.Thread(target=self._serve, args=(s, door, stop), daemon=True, name=f"door-{launch}")
            self.open[launch] = (s, stop, t)
            t.start()

    def _serve(self, s, door, stop):
        import socket
        import threading
        while not stop.is_set():
            try:
                c, _ = s.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=serve_one, args=(c, door), daemon=True).start()

    def close(self, launch):
        s, stop, _ = self.open.pop(launch)
        stop.set()
        try:
            path = s.getsockname()
            s.close()
            if path and os.path.lexists(path):
                os.unlink(path)
        except OSError:
            pass
