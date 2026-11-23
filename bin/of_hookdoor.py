"""The hooks door: where a Claude Code session asks the listener for its organization's hook decision (of_server's
ruling, message 01a20566-e23c, relayed by of_bench, message 01a20569-9699).

An organization's Lua (its hooks and settings) is evaluated only inside Bench's operating-system sandbox, which cannot
start inside a session's sandbox (docs/bench.md says where that happens on each system). So `of ask`'s hook before the
module "file", when it runs in a Claude Code session, sends this request instead. The request is data only: the
organization, the package and module names and the card's fields, each on an allowlist with its type. The listener
takes the caller's organization from the process on the other end of the socket (its Claude Code session, mapped to
its position by the hook's session index), the organization's folder from its own orgs.yaml, never from the request,
and the hook's code from that folder; it answers with the decision alone.
"""

import json
import os
import re
import socket
import threading

V = 1
KIND = "hook_before"
MAX_REQUEST = 64 * 1024
READ_S = 10
NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}")
POSITION = re.compile(r"[a-z][a-z0-9_-]{0,31}")
TAG = re.compile(r"[a-z0-9_:-]{1,64}")
# the card's fields: name -> (kind, required)
CARD = {"question": ("text", True), "options": ("texts", False), "tags": ("tags", False),
        "tags_guessed": ("tags", False), "to": ("position", False), "from": ("position", False),
        "blocking": ("bool", False), "due": ("text?", False), "task": ("bool", False)}
ANSWER = ("decision", "by", "reason", "to", "until")


# ---- the peer process (read here, so this door stands on its own) ----

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


def peer_pid(sock):
    """The connecting process's pid: SO_PEERCRED on Linux, LOCAL_PEERPID on macOS; None when unknown."""
    import struct
    try:
        if hasattr(socket, "SO_PEERCRED"):
            return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[0]
        return struct.unpack("i", sock.getsockopt(0, 2, 4))[0]   # SOL_LOCAL, LOCAL_PEERPID
    except OSError:
        return None


class Refused(Exception):
    pass


def socket_path():
    """Outside every session's sandbox writes on Linux (the runtime folder), so no session can put its own socket
    there; on the Mac, where sessions have no sandbox, OF's state folder."""
    import sys
    run = os.environ.get("XDG_RUNTIME_DIR")
    if sys.platform.startswith("linux") and run and os.path.isdir(run):
        return os.path.join(run, "of", "hooks.sock")
    import of_events
    return os.path.join(of_events.STATE_DIR, "listen", "hooks.sock")


def _text(v, what, limit=8000):
    if not isinstance(v, str) or len(v) > limit or "\x00" in v:
        raise Refused(f"{what}: a text of at most {limit} characters, with no NUL")
    return v


def validate(req):
    """REQ as the listener reads it, or Refused: every key and type on the allowlist, nothing else."""
    if not isinstance(req, dict) or set(req) != {"v", "kind", "org", "package", "module", "card"}:
        raise Refused("a request is {v, kind, org, package, module, card} and nothing else")
    if req["v"] != V or req["kind"] != KIND:
        raise Refused(f"only v {V}, kind {KIND}")
    for k in ("org", "package", "module"):
        if not isinstance(req[k], str) or not NAME.fullmatch(req[k]):
            raise Refused(f"{k}: a name such as of-asks")
    card = req["card"]
    if not isinstance(card, dict) or set(card) - set(CARD):
        raise Refused(f"card: only {', '.join(sorted(CARD))}")
    out = {}
    for k, (kind, required) in CARD.items():
        if k not in card:
            if required:
                raise Refused(f"card: {k} is required")
            continue
        v = card[k]
        if kind == "text":
            out[k] = _text(v, f"card.{k}")
        elif kind == "text?":
            out[k] = None if v is None else _text(v, f"card.{k}", 64)
        elif kind == "texts":
            if not isinstance(v, list) or len(v) > 20:
                raise Refused(f"card.{k}: a list of at most 20 texts")
            out[k] = [_text(x, f"card.{k}", 500) for x in v]
        elif kind == "tags":
            if not isinstance(v, list) or len(v) > 20 or not all(isinstance(x, str) and TAG.fullmatch(x) for x in v):
                raise Refused(f"card.{k}: a list of at most 20 tags")
            out[k] = list(v)
        elif kind == "position":
            if not isinstance(v, str) or not (POSITION.fullmatch(v) or v in ("founder", "owner")):
                raise Refused(f"card.{k}: a position's name")
            out[k] = v
        elif kind == "bool":
            if not isinstance(v, bool):
                raise Refused(f"card.{k}: true or false")
            out[k] = v
    return {"v": V, "kind": KIND, "org": req["org"], "package": req["package"], "module": req["module"], "card": out}


SESSION_FLAG = re.compile(r"^--(?:session-id|resume)(?:=(.+))?$")
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _uid(pid):
    """The user id PID runs as (Linux /proc, else ps); None when unknown."""
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("Uid:"):
                    return int(line.split()[1])
    except OSError:
        pass
    import subprocess
    try:
        return int(subprocess.run(["ps", "-o", "uid=", "-p", str(pid)], capture_output=True, text=True,
                                  timeout=5).stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def org_of_session(sid, index=None):
    """SID's organization by the session index the hook writes (OF's state folder, index.json; denied to every
    session's writes), never by anything in the request; None when the index does not name it."""
    import of_events
    try:
        idx = index if index is not None else json.loads(
            of_events.read_own(os.path.join(of_events.STATE_DIR, "index.json"), cap=64 << 20))
        row = (idx.get("sessions") or {}).get(sid)
        return row[1] if isinstance(row, list) and len(row) > 1 and isinstance(row[1], str) else None
    except (OSError, ValueError, AttributeError):
        return None


def caller_session(pid, ancestors=None, cmdline=None, uid=None):
    """The Claude Code session the process PID runs under: the nearest ancestor that is a Claude Code process of this
    user, by the session id it was started with (--session-id or --resume); None when there is none, the tree reaches
    a Codex process first, or the Claude Code process is another user's."""
    ancestors = ancestors or _ancestors
    cmdline = cmdline or _cmdline
    for p in ancestors(pid):
        cl = cmdline(p)
        if cl and os.path.basename(cl[0]).startswith("codex"):
            return None   # a Codex session is not a Claude Code session: no caller here
        if not cl or not any(os.path.basename(a) == "claude" or a.endswith("/claude") for a in cl[:2]):
            continue
        if (uid or _uid)(p) != os.getuid():
            return None   # a Claude Code process of another user
        for i, a in enumerate(cl):
            m = SESSION_FLAG.match(a)
            if m:
                sid = m.group(1) or (cl[i + 1] if i + 1 < len(cl) else "")
                return sid if UUID.fullmatch(sid) else None
        return None   # the nearest Claude Code process names no session: no guess further up
    return None


def answer(req, caller_org, project_of, evaluate):
    """The decision for a validated REQ from a caller of CALLER_ORG: refused for another organization's hooks."""
    if not caller_org:
        raise Refused("the caller is no position's session")
    if req["org"] != caller_org:
        raise Refused("a session asks only its own organization's hooks")
    project = project_of(req["org"])
    if not project:
        raise Refused(f"no folder for {req['org']} in this machine's orgs.yaml")
    d = evaluate(project, req["package"], req["module"], req["card"])
    return {k: d[k] for k in ANSWER if d.get(k) is not None}


def _recv(conn, limit, deadline_s):
    """The request, within one deadline for the whole of it, never per read: a client sending a byte now and then
    would hold its thread as long as it liked (of_server's review of the door)."""
    import time
    end = time.monotonic() + deadline_s
    buf = b""
    while True:
        left = end - time.monotonic()
        if left <= 0:
            raise Refused(f"the request did not arrive within {deadline_s} s")
        conn.settimeout(left)
        try:
            chunk = conn.recv(65536)
        except socket.timeout:
            raise Refused(f"the request did not arrive within {deadline_s} s")
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise Refused(f"a request is at most {limit} bytes")
        if buf.endswith(b"\n"):
            break
    return buf


def serve_one(conn, org_of_session, project_of, evaluate, peer=None, session_of=caller_session):
    """Read one request from CONN, answer it, close it."""
    try:
        try:
            raw = _recv(conn, MAX_REQUEST, READ_S)
            if b"\x00" in raw:
                raise Refused("a NUL in the request")
            req = validate(json.loads(raw.decode("utf-8")))
            sid = session_of((peer or peer_pid)(conn))
            out = {"ok": True, "answer": answer(req, org_of_session(sid) if sid else None, project_of, evaluate)}
        except (Refused, ValueError, UnicodeDecodeError) as e:
            out = {"ok": False, "refused": str(e)[:500]}
        except Exception as e:  # noqa: BLE001 - an error the caller reads as a stop, never a pass
            out = {"ok": False, "refused": f"{type(e).__name__}: {str(e)[:300]}"}
        conn.sendall((json.dumps(out) + "\n").encode("utf-8"))
    except OSError:
        pass
    finally:
        conn.close()


def serve(org_of_session, project_of, evaluate, path=None, stop=None, log=print, session_of=caller_session,
          ready=None):
    """Serve the hooks door until STOP: one socket, mode 600 in this user's own 700 folder; one evaluation at a time
    per organization."""
    path = path or socket_path()
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    import stat
    st = os.lstat(folder)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700:
        # its own folder, mode 700, no link, or no door (of_server's review of the shape, message 01a20573-e73b)
        raise OSError(f"{folder}: not this user's own folder of mode 700; the hooks door is not served")
    try:
        st = os.lstat(path)
        if stat.S_ISSOCK(st.st_mode) and st.st_uid == os.getuid():
            os.unlink(path)   # this listener's own from before; anything else stays and the bind fails
    except FileNotFoundError:
        pass
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        s.bind(path)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    s.listen(16)
    s.settimeout(1.0)
    if ready is not None:
        ready.set()
    # one evaluation at a time per organization, the rest waiting; the client's own timeout is the only bound
    # (of_server: a cap would fail a card's filing under load, and nobody asked for one)
    locks, guard = {}, threading.Lock()

    def queued(project, package, module, card):
        with guard:
            lock = locks.setdefault(project, threading.Lock())
        with lock:
            return evaluate(project, package, module, card)

    def handle(conn):
        serve_one(conn, org_of_session, project_of, queued, session_of=session_of)
    while not (stop and stop.is_set()):
        try:
            conn, _ = s.accept()
        except socket.timeout:
            continue
        except OSError as e:
            log(f"hooks door: {e}")
            continue
        threading.Thread(target=handle, args=(conn,), daemon=True, name="of-hookdoor-req").start()
    s.close()


def ask(org, package, module, card, path=None, timeout=60):
    """From a session: the listener's decision for CARD, as of_method.before gives it; a stop with the reason when
    the door cannot be reached or refuses, never a pass."""
    by = "the hooks door"
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(path or socket_path())
        s.sendall((json.dumps({"v": V, "kind": KIND, "org": org, "package": package, "module": module,
                               "card": card}) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        r = json.loads(buf.decode("utf-8"))
    except (OSError, ValueError) as e:
        return {"decision": "stop", "by": by, "reason": f"the listener's hooks door cannot be reached: {e}"}
    if not r.get("ok"):
        return {"decision": "stop", "by": by, "reason": f"refused: {r.get('refused')}"}
    a = r.get("answer") or {}
    return {k: a[k] for k in ANSWER if k in a} if a.get("decision") else \
        {"decision": "stop", "by": by, "reason": "an answer with no decision"}
