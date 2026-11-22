"""Append events to the machine's Organic Framework event log (docs/events.md, schema v1)."""

import fcntl
import json
import re
import os
import socket
import time
import uuid

STATE_DIR = os.environ.get("OF_STATE") or os.path.expanduser("~/.local/state/of")
# The logs OF's services and their watchdogs write from outside any sandbox live here, never in STATE_DIR, which every
# sandboxed session may write on the agent host: there systemd opens a unit's append: log itself and follows a link a
# session put at its name, and no O_NOFOLLOW in OF's code covers it (of_agent-server, msg 01a20460-c4bf; of_server's
# hand-over 58681935). Outside spawn's allowWrite.
LOG_DIR = os.path.expanduser("~/.local/log/of")   # no environment override: the installing shell would choose the
# units' text (of_review on PR 545 and PR 543)
CONFIG_DIR = os.environ.get("OF_CONFIG_DIR") or os.path.expanduser("~/.config/of")


def config_path(name):
    """A file in the OF config dir: ~/.config/of, or $OF_CONFIG_DIR (tests, labs, a second profile)."""
    return os.path.join(CONFIG_DIR, name)
SCHEMA = 1


def _state_dir():
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    return STATE_DIR


_FALLBACK_ID = []   # machine_id's own id for this process, when no id file can be made or read


def machine_id(wait_s=5.0):
    """This machine's id, made once. Several processes may make it at once on a fresh state folder (the agent host's
    listener and relay, started together: of_review on organic-framework PR 409, where 3 of 4 threads failed the
    O_EXCL create and a reader could find the file empty). The id is written whole into a temporary file and linked
    into place; a caller whose link loses reads the winner's, waiting for it to be non-empty."""
    path = os.path.join(_state_dir(), "machine_id")

    def read():
        if os.path.islink(path):   # a link reads as no id, never through it: the repair below replaces it
            return None
        try:   # read_own (of_server on PR 545); two names while link_new's temporary file is still there
            return read_own(path, cap=4096, max_links=2).strip()
        except (FileNotFoundError, IsADirectoryError):   # missing, a folder in its place: no id
            return None

    def fallback():
        if not _FALLBACK_ID:
            _FALLBACK_ID.append(str(uuid.uuid4()))
        return _FALLBACK_ID[0]
    try:
        first = read()
    except OSError:   # an id this process may not read (PermissionError): never replaced, the process's own id
        return fallback()   # instead (of_review on organic-framework PR 423)
    if first:
        return first
    import tempfile
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)

    def link_new():
        """Link a fresh id into place; False when another caller's file is there."""
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".machine_id.")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(str(uuid.uuid4()) + "\n")
            os.chmod(tmp, 0o600)
            os.link(tmp, path)
            return True
        except FileExistsError:
            return False
        finally:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
    deadline, rounds = time.time() + wait_s, 0
    while True:
        try:
            mid = read()
        except OSError:
            return fallback()
        if mid:
            return mid
        if mid is None and link_new():
            continue   # won: the next read gives this id
        if mid is None and not os.path.lexists(path):
            continue   # lost to a caller whose file is in place now
        if time.time() > deadline:
            rounds += 1
            if rounds > 2:   # a state folder no caller can fix: an id for this process, never a hang (every emit and
                # hook asks for it; of_review on organic-framework PR 419), the same one for the process's life, so
                # its events carry one host.id (of_review's note after PR 419)
                return fallback()
            # an empty file an older writer left: one caller at a time, under a lock, replaces it if it is still empty,
            # and the others then read that id (of_review's note on organic-framework PR 414: os.replace per caller
            # gave six processes two ids)
            import fcntl
            try:
                lock = os.fdopen(open_own(os.path.join(d, ".machine_id.lock")), "a")   # open_own (PR 545)
            except OSError:
                return fallback()   # an id is always given
            with lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    cur = read()
                except OSError:
                    return fallback()
                if cur == "" or (cur is None and os.path.lexists(path)):   # empty, or a link to nothing
                    fd, tmp = tempfile.mkstemp(dir=d, prefix=".machine_id.")
                    try:
                        with os.fdopen(fd, "w") as fh:
                            fh.write(str(uuid.uuid4()) + "\n")
                        os.chmod(tmp, 0o600)
                        os.replace(tmp, path)
                    except OSError:   # a folder in its place: left as it is; the rounds end in this process's own id
                        os.unlink(tmp)
                    except BaseException:
                        os.unlink(tmp)
                        raise
            deadline = time.time() + wait_s
            continue
        time.sleep(0.02)


def _uuid7():
    u7 = getattr(uuid, "uuid7", None)
    return str(u7() if u7 else uuid.uuid4())


_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


_MODEL_CWD = None


def model_cwd():
    """The working folder of a model call OF makes outside the sandbox (claude -p for the case, the judges, the
    corrections, the tempo reading): made fresh once per process, mode 700, under ~/.cache/of, where no session's
    sandbox writes, so no project settings or instructions are planted where the call reads them (the folders inside
    OF's state folder that served before are writable from a session's sandbox on Linux). Removed at exit."""
    global _MODEL_CWD
    if _MODEL_CWD and os.path.isdir(_MODEL_CWD) and not os.path.islink(_MODEL_CWD):
        return _MODEL_CWD
    import atexit
    import shutil
    import tempfile
    base = os.path.join(os.path.expanduser("~"), ".cache", "of")
    os.makedirs(base, mode=0o700, exist_ok=True)
    _MODEL_CWD = tempfile.mkdtemp(prefix="model-cwd-", dir=base)
    atexit.register(shutil.rmtree, _MODEL_CWD, True)
    return _MODEL_CWD


def log_path(name):
    """The path of the log NAME in LOG_DIR, the folder made (and refused when it is a link or not ours)."""
    return os.path.join(own_dir(LOG_DIR), name)


def open_own(path, flags=os.O_WRONLY | os.O_CREAT | os.O_APPEND, mode=0o600):
    """A descriptor for PATH in OF's state folder, never through a link put at its name. On the agent host every
    sandboxed session may write that folder, and the hook's and the store's processes run outside the sandbox, so a link
    there would have them write into any file of their user's (docs/guard.md, Gaps, "A process outside the sandbox
    follows a link"; of_server, hand-over 58681935). Anything but a regular file of ours with one link is refused,
    and a FIFO fails at once (O_NONBLOCK). The relay's _open_own is the model."""
    import stat
    fd = os.open(path, flags | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0), mode)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink > 1:   # 0: replaced after open
        os.close(fd)
        raise OSError(f"{path} is not a regular file of ours")
    return fd


def read_own(path, cap=1 << 20, max_links=1):
    """The text of PATH in OF's state folder: no link, a regular file of this user with at most MAX_LINKS names (a file
    replaced after open has 0, and stays readable), no wait on open, at most CAP bytes (of_server on PR 545).
    FileNotFoundError when it is not there; OSError when it is refused."""
    import stat
    fd = os.open(path, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink > max_links:
            raise OSError(f"{path} is not a regular file of ours")
        if st.st_size > cap:
            raise OSError(f"{path} is larger than {cap} bytes")
        with os.fdopen(os.dup(fd), "rb") as fh:
            return fh.read(cap).decode("utf-8", "replace")
    finally:
        os.close(fd)


def append_own(path, text):
    """Append TEXT to PATH through open_own."""
    with os.fdopen(open_own(path), "a") as fh:
        fh.write(text)


def own_dir(path, mode=0o700):
    """Make the folder PATH (and its parents) and refuse it when it is a link or not ours: a file written under a
    folder a session replaced by a link would land wherever the link points."""
    import stat
    os.makedirs(path, mode=mode, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise OSError(f"{path} is not a folder of ours")
    return path


def write_new(path, text, mode=0o600):
    """Replace PATH with TEXT through a new file beside it, made with O_EXCL and never through a link (whatever lies at
    the temporary name is removed first, and one planted meanwhile stops the write), then renamed over PATH: a link at
    PATH is replaced, never followed. The relay's _open_new is the model."""
    import threading
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"   # and the thread, as the relay names it
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_iso(ts=None):
    ts = time.time() if ts is None else ts
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int(ts * 1000) % 1000:03d}Z"


def record(kind, payload, *, position=None, org=None, session=None, source="of", extra=None):
    """Build one event record. `kind` is the bare kind; event.name gets the of. prefix."""
    rec = {
        "of.schema": SCHEMA,
        "of.event.id": _uuid7(),
        "timestamp": now_iso(),
        "event.name": f"of.{kind}",
        "host.id": machine_id(),
        "host.name": socket.gethostname(),
        "gen_ai.conversation.id": session,
        "gen_ai.agent.name": position,
        "of.org": org,
        "of.source": source,
        # JSON-safe: a value JSON cannot hold (a YAML date, a set) is written as text, never dropped
        "of.payload": json.loads(json.dumps(payload, ensure_ascii=False, default=str)),
    }
    rec.update(extra or {})
    if not rec.get("of.org"):  # resolve from the position, else from the working directory
        try:
            import of_hook
            rec["of.org"] = of_hook.resolve_org(position, rec.get("of.cwd") or os.getcwd() if source != "of" else
                                                rec.get("of.cwd"))
        except Exception:
            pass
    run = os.environ.get("OF_LAB_RUN_ID")
    if run:  # a lab run: its own organization of the tenant `lab` (docs/lab.md)
        rec["of.lab.run_id"] = run
        rec["of.org"] = f"lab-{run}"
    if kind in ("agent_question", "permission_request") and source != "import" and isinstance(rec["of.payload"], dict) \
            and not rec["of.payload"].get("ref") and rec["of.payload"].get("detected_by") != "heuristic" \
            and not (kind == "agent_question" and rec["of.payload"].get("subagent")):
        # nor a subagent's question, which the projection never shows (of_asks.relevant): every stamped ask is one the
        # asks show, so ref_id, card_name and the projection's suffixes count the same set (of_review on PR 300)
        # a heuristic candidate is no card until the judge files it as one, which is stamped then (of_asks on PR 300:
        # candidates took five numbers in six)
        try:  # the card's short id, from the organization's server when it is filed (every writer comes through here);
            # a hook never waits on the network (Claude Code's hook timeout), nor the listener, which files asks
            # inside a write of the store (of_release on PR 496); their asks get
            # theirs from the pulse (backfill_refs), and `of ask` takes its id before it files
            ref = card_ref(rec.get("of.org"), rec["of.event.id"]) \
                if HOOK_TIMEOUT is None and not CARD_REFS_LATER else None
            if ref:
                rec["of.payload"]["ref"] = ref
        except Exception:  # noqa: BLE001 - an ask is never lost for its id: the pulse gets it later (backfill_refs)
            pass
    return rec


# ------------------------------------------------------------------ card ids

REF_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]{0,7})-(\d+)([a-z]?)$")


def card_prefix(org):
    """An organization's card prefix: the registry's `card_prefix`, else its name's initials (acme-shop: AS)."""
    try:
        import of_hook
        set_ = ((of_hook._registry_index() or {}).get("card_prefix") or {}).get(org)
        if set_:
            return str(set_).upper()
    except Exception:  # noqa: BLE001
        pass
    return default_prefix(org)


def default_prefix(org):
    """An organization's name's initials (acme-shop: AS), or a one-word name's first three letters (acme: ACM), so the
    prefix keeps to 2 to 5 letters (docs/registry.md, Card ids; of_architect on PR 300)."""
    words = [w for w in re.split(r"[-_. ]+", re.sub(r"[^A-Za-z_. -]", "", str(org or "of"))) if w]
    if len(words) == 1:
        return words[0][:3].upper()
    return ("".join(w[0] for w in words).upper() or "OF")[:5]


def _refs_path():
    return os.path.join(STATE_DIR, "ask-refs.json")


def _with_refs(fn):
    """FN(counters) under the counters' lock; it changes them in place; returns its result."""
    import fcntl
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    with os.fdopen(open_own(_refs_path() + ".lock", os.O_WRONLY | os.O_CREAT), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:   # read_own (of_server on PR 545); absent is no counter yet, refused gives no number and writes nothing
            counters = json.loads(read_own(_refs_path()))
            counters = counters if isinstance(counters, dict) else {}
        except FileNotFoundError:
            counters = {}
        except ValueError:
            counters = {}
        out = fn(counters)
        write_new(_refs_path(), json.dumps(counters))
        return out


def next_ref(org):
    """The next card id of ORG: its prefix and the next number of its counter on this machine (ask-refs.json), which
    seen_ref raises to the highest number another machine used (the coordination feed)."""
    prefix = card_prefix(org)

    def take(c):
        c[prefix] = int(c.get(prefix) or 0) + 1
        return f"{prefix}-{c[prefix]}"
    try:
        return _with_refs(take)
    except OSError:   # the counter's file refused: no id now (a number given again would cite another card); the
        return None   # pulse backfills it (of_review on PR 545)


CARD_REF_TIMEOUT_S = 3   # a filing waits this long for its server; past it the ask is filed without its id
CARD_REFS_LATER = False  # set by the listener, whose processors file asks inside a write of the store


def _local_highest(prefix):
    try:
        return _with_refs(lambda c: int(c.get(prefix) or 0))
    except OSError:
        return 0


def card_ref(org, event_id, timeout=CARD_REF_TIMEOUT_S, client=None):
    """The card id of the ask EVENT_ID of ORG, from its organization's server, which gives each number out once and
    the same number again for the same event (of_architect, msg 01a1faa1-1fa0: card ids collided when two machines
    numbered on their own counters with the coordination feed cut). The request carries this machine's highest
    number, so the server's counter starts above every store's. None when the server cannot be reached: the card
    shows its short event id until the pulse gets the number (backfill_refs). A lab run numbers on its machine."""
    prefix = card_prefix(org)
    url = None if client is not None else _org_server_url(org)
    if os.environ.get("OF_LAB_RUN_ID") or not event_id or (client is None and not url):
        return next_ref(org)   # no server of its own in orgs.yaml: its one machine numbers its asks
    try:
        if client is None:
            import of_client
            client = of_client.Client(url)
        ref = client.card_ref(org, prefix, event_id, _local_highest(prefix), timeout=timeout)
    except Exception as e:  # noqa: BLE001 - no server answer: no id now, the pulse asks again
        if getattr(e, "status", None) in (404, 405):
            return next_ref(org)   # a server without the counter yet: this machine numbers, as before
        return None
    if not (isinstance(ref, str) and REF_RE.match(ref) and ref.upper().startswith(prefix + "-")):
        return None
    seen_ref(ref)
    return ref


def _org_server_url(org, orgs_file=None):
    """The server ORG's orgs.yaml entry names (`server:`), else None."""
    try:
        import yaml
        with open(orgs_file or os.environ.get("OF_ORGS") or config_path("orgs.yaml")) as fh:
            doc = yaml.safe_load(fh) or {}
    except Exception:  # noqa: BLE001
        return None
    for o in doc.get("orgs") or []:
        if isinstance(o, dict) and org in (o.get("name"), o.get("id")) and o.get("server"):
            return str(o["server"]).rstrip("/")
    return None


def backfill_refs(asks, here=None, log=print, ask=None):
    """The open asks this machine filed without a card id get one from their server, recorded as of.ask_ref (its fixed
    id, so a second pass or another machine records it once). Returns how many got one."""
    here = here or machine_id()
    n = 0
    for a in sorted(asks, key=lambda x: x.get("ts") or ""):
        if a.get("ref") or a.get("state") not in ("open", "escalated") or (a.get("host_id") or here) != here:
            continue
        org = a.get("org") or ask_org(a["event_id"])
        ref = (ask or card_ref)(org, a["event_id"])
        if not ref:
            continue
        emit("ask_ref", {"ask_id": a["event_id"], "ref": ref}, event_id=stable_id("ask_ref", a["event_id"]), org=org,
             position=a.get("position"))
        log(f"of pulse: {ref} for the ask {a['event_id'][-8:]} filed without its id")
        n += 1
    return n


def seen_ref(ref):
    """Raise the counter of REF's prefix to its number (an ask filed on another machine, or a backfill)."""
    m = REF_RE.match(str(ref or ""))
    if not m:
        return

    def raise_(c):
        prefix, n = m.group(1).upper(), int(m.group(2))
        if n > int(c.get(prefix) or 0):
            c[prefix] = n
    try:
        _with_refs(raise_)
    except OSError:   # a refused counter file: the number the server gave stands, the counter is not raised
        pass


HOOK_TIMEOUT = None  # set by `of hook`: wait at most this long for the database, then spool
_CONN = {}


def _conn():
    import of_store
    import threading
    key = (of_store.db_path(), HOOK_TIMEOUT, threading.get_ident())  # SQLite objects stay in their thread
    if key not in _CONN:
        _CONN[key] = of_store.connect(timeout=HOOK_TIMEOUT or 10.0)
    return _CONN[key]


def append(rec, raw_ref=None):
    """Write one record to the local store (of.db); if it is busy or broken, spool it to disk
    for the ingest worker. Never raises."""
    try:
        import of_store
        of_store.add_event(_conn(), rec, raw_ref)
        return True
    except Exception:
        pass
    try:
        import of_store
        of_store.spool_append("events.jsonl", rec)
        return True
    except Exception:
        ok = append_jsonl(rec)
        if not ok:  # nowhere to write: say so where the owner looks, instead of losing it silently
            try:
                append_own(os.path.join(_state_dir(), "hook-errors.log"),
                           f"{now_iso()} ERROR event {rec.get('event.name')} {rec.get('of.event.id')} not written\n")
            except Exception:
                pass
        return ok


def append_jsonl(rec):
    """The fallback spool: one JSON line under an exclusive lock. Never raises."""
    try:
        path = os.path.join(_state_dir(), "events.jsonl")
        fd = open_own(path)   # never through a link: it runs when the store and the spool refuse (of_review, PR 545)
        with os.fdopen(fd, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        return True
    except Exception:
        return False


ASK_CLOSURES = ("agent_question_answered", "ask_withdrawn", "ask_escalated")


def ask_org(ask_id):
    """The organization of an ask, from its own event (None if unknown or the store is busy)."""
    try:
        row = _conn().execute("SELECT org FROM event WHERE event_id = ?", (ask_id,)).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def emit(kind, payload, event_id=None, **kw):
    """Write one event. Returns its id, or None if it could not be written anywhere.
    A closure of an ask carries the ask's organization when the caller gave none (reconcile, the CLI and
    the popup close asks of any organization; a per-organization model must see the closure).
    event_id: a fixed id (stable_id) for an event that more than one machine may write about the same thing; the
    store keeps the first copy and the coordination feed drops the rest (docs/server-several-machines.md §4.3)."""
    if kind in ASK_CLOSURES and not kw.get("org") and isinstance(payload, dict):
        aid = payload.get("ask_id") or (payload.get("ask_ids") or [None])[0]
        if aid:
            kw["org"] = ask_org(aid)
    rec = record(kind, payload, **kw)
    if event_id:
        rec["of.event.id"] = event_id
    return rec["of.event.id"] if append(rec) else None


STABLE_NS = uuid.UUID("5b0f6a3e-2f1c-4c8e-9a43-0f0c00000000")


def stable_id(*parts):
    """The same id for the same fact on every machine: uuid5 of its parts."""
    return str(uuid.uuid5(STABLE_NS, ":".join(str(p) for p in parts)))
