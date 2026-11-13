"""of capacity: model limits as a budgeted resource (docs/capacity.md).

The founder has several Claude accounts, one per config dir (`~/.claude`, `~/.claude-<name>`).
Each has a 5-hour and a 7-day window. When one runs out, the positions running on it move to
another: a Workplace change with the same holder (RFC 0001 v3 §5.6).

  status   per config dir: the account, logged in or not, both windows (utilization, reset), and
           the positions and sessions running on it. The reading is a minimal `claude -p` probe on
           the cheapest model, at most once per PROBE_EVERY_MIN minutes; each fresh reading is an
           `of.capacity_snapshot` event with the probe's token cost.

Pure functions take their inputs; the CLI (`bin/of`) wires them to the registries and the disk.
"""

import calendar
import glob
import json
import os
import subprocess
import sys
import time

import of_events

STATE_DIR = of_events.STATE_DIR
PROBE_EVERY_MIN = 30
PROBE_MODEL = "haiku"
PROBE_PROMPT = "Reply with the single word: ok"
PROBE_TIMEOUT_S = 120
THRESHOLDS = {"seven_day": 90, "five_hour": 95}   # a position's account past these: propose a move
WINDOWS = ("five_hour", "seven_day")


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _ts(iso):
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return None


# ------------------------------------------------------------ config dirs

def config_dirs(home=None):
    """The machine's accounts, sorted (docs/accounts.md): the folders listed in ~/.config/of/accounts.yaml, else
    `~/.claude` and every `~/.claude-<name>` directory (of_accounts.discovered)."""
    import of_accounts
    return of_accounts.dirs(home)


def short(d, home=None):
    home = home or os.path.expanduser("~")
    return "~" + d[len(home):] if d.startswith(home + "/") else d


def account_file(d, home=None):
    """The default dir keeps its account in ~/.claude.json; a named dir in <dir>/.claude.json."""
    home = home or os.path.expanduser("~")
    if os.path.normpath(d) == os.path.join(home, ".claude"):
        return os.path.join(home, ".claude.json")
    return os.path.join(d, ".claude.json")


def account_of(d, home=None):
    try:
        with open(account_file(d, home)) as fh:
            return (json.load(fh).get("oauthAccount") or {}).get("emailAddress") or None
    except Exception:
        return None


# --------------------------------------------------------------- sessions

def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def live_sessions(d, alive=_alive):
    """The harness's live sessions in this config dir: `<dir>/sessions/<pid>.json` whose pid runs."""
    out = []
    for path in sorted(glob.glob(os.path.join(d, "sessions", "*.json"))):
        try:
            with open(path) as fh:
                j = json.load(fh)
            if not alive(int(j["pid"])):
                continue
            out.append({"pid": j["pid"], "session": j.get("sessionId"), "status": j.get("status"),
                        "name": j.get("name"), "cwd": j.get("cwd"), "file": path, "started": j.get("startedAt")})
        except Exception:
            continue
    return out


def positions_by_dir(positions, sessions):
    """sessions: {config dir: live_sessions(dir)}. Returns {dir: {positions, others, sessions, busy}}:
    the registry positions whose holder session runs there, the live sessions no position claims."""
    by_session = {}
    for p in positions:
        s = ((p.get("holder") or {}).get("session") or "").strip()
        if s:
            by_session[s] = p
    out = {}
    for d, ss in sessions.items():
        names, others, by_org = [], [], {}
        for s in ss:
            p = by_session.get(s["session"])
            if p:
                names.append(p["name"])
                by_org.setdefault(p.get("_org") or "?", []).append(p["name"])
            else:
                others.append(s.get("name") or (s.get("session") or "")[:8])
        out[d] = {"positions": names, "by_org": by_org, "others": others, "sessions": len(ss),
                  "busy": sum(1 for s in ss if s.get("status") == "busy")}
    return out


def dir_of_position(p, sessions):
    s = ((p.get("holder") or {}).get("session") or "").strip()
    for d, ss in sessions.items():
        if any(x["session"] == s for x in ss):
            return d
    return None


# ------------------------------------------------------------------ probe

def is_default(d, home=None):
    """`~/.claude` is the default dir: the harness uses it with CLAUDE_CONFIG_DIR *unset*. Setting the
    variable to ~/.claude gives a different identity (`~/.claude/.claude.json`, not `~/.claude.json`),
    which is not logged in: a probe or a resume must unset it instead (found 2026-11-06)."""
    home = home or os.path.expanduser("~")
    return os.path.normpath(os.path.expanduser(d)) == os.path.join(home, ".claude")


def probe_env(d, home=None):
    """The environment of every automation step (AGENTS.md): the target config dir, no herdr binding
    for the nested run, no SSH agent, OF_AUTOMATION."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_") and k != "CLAUDE_CODE_SESSION_ID"}
    env.update({"CLAUDE_CONFIG_DIR": d, "OF_AUTOMATION": "1", "SSH_AUTH_SOCK": "", "OF_PROBE": "1"})
    import pwd  # the harness finds its login in the keychain by user name: a bare job environment lacks it
    user = pwd.getpwuid(os.getuid()).pw_name
    env.setdefault("USER", user)
    env.setdefault("LOGNAME", user)
    env.setdefault("HOME", os.path.expanduser("~"))
    if is_default(d, home):
        env.pop("CLAUDE_CONFIG_DIR")
    return env


def claude_bin():
    """launchd and systemd jobs run with a short PATH (no ~/.local/bin): look in the usual places too."""
    import shutil
    found = shutil.which("claude")
    if found:
        return found
    for c in ("~/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"):
        c = os.path.expanduser(c)
        if os.path.exists(c):
            return c
    return "claude"


def probe_command(model=PROBE_MODEL):
    """No hooks (the probe is not a session of any position, so the recorder must not see it) and no MCP
    servers: half the cost of a plain call, and nothing spawned."""
    return [claude_bin(), "-p", PROBE_PROMPT, "--model", model, "--output-format", "stream-json", "--verbose",
            "--max-turns", "1", "--settings", '{"disableAllHooks": true}', "--strict-mcp-config"]


def pct(u):
    """Utilization as a whole percent. The CLI reports a fraction (0.99) today; older builds whole percents."""
    if u is None:
        return None
    return int(round(u * 100)) if isinstance(u, float) or u <= 1 else int(u)


def parse_probe(lines, stderr, returncode):
    """The reading from one probe's stream: the `rate_limit_event` (windows) and the `result` (cost)."""
    r = {"logged_in": False, "status": None, "overage": None, "windows": {}, "error": None,
         "probe": {"model": None, "cost_usd": None, "tokens": None, "duration_ms": None}}
    result = None
    for line in lines:
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if j.get("type") == "rate_limit_event":
            info = j.get("rate_limit_info") or {}
            r["logged_in"] = True
            r["status"] = info.get("status")
            r["overage"] = info.get("overageDisabledReason") or info.get("overageStatus")
            for w, v in (info.get("unifiedWindows") or {}).items():
                r["windows"][w] = {"utilization": pct(v.get("utilization")), "status": v.get("status"),
                                   "resets_at": _iso(v["resetsAt"]) if v.get("resetsAt") else None}
        elif j.get("type") == "result":
            result = j
    if result:
        u = result.get("usage") or {}
        mu = result.get("modelUsage") or {}
        r["probe"].update({
            "model": next(iter(mu), None),
            "cost_usd": result.get("total_cost_usd"),
            "duration_ms": result.get("duration_ms"),
            "tokens": {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                       "cache_read": u.get("cache_read_input_tokens", 0),
                       "cache_creation": u.get("cache_creation_input_tokens", 0)}})
        if result.get("is_error") and not r["logged_in"]:
            r["error"] = str(result.get("result") or result.get("subtype"))[:300]
    if not r["logged_in"]:
        err = (stderr or "").strip().splitlines()
        r["error"] = r["error"] or (err[-1][:300] if err else f"no reading (exit {returncode})")
        r["probe"]["cost_usd"] = r["probe"]["cost_usd"] if result else None
        # only the harness saying so means "not logged in"; a missing binary or a timeout is no reading
        said = " ".join([r["error"] or "", stderr or "", str((result or {}).get("result") or "")]).lower()
        r["logged_in"] = False if ("not logged in" in said or "/login" in said) else None
    return r


def probe(d, now=None, runner=subprocess.run):
    """One minimal call on the cheapest model, from a neutral cwd (no project instructions to load)."""
    now = now or time.time()
    cwd = os.path.join(STATE_DIR, "capacity", "probe")
    os.makedirs(cwd, mode=0o700, exist_ok=True)
    try:
        out = runner(probe_command(), cwd=cwd, env=probe_env(d), capture_output=True, text=True,
                     timeout=PROBE_TIMEOUT_S)
        r = parse_probe(out.stdout.splitlines(), out.stderr, out.returncode)
    except FileNotFoundError:
        r = parse_probe([], "claude not found", 127)
    except subprocess.TimeoutExpired:
        r = parse_probe([], f"probe timed out after {PROBE_TIMEOUT_S}s", 124)
    r.update({"config_dir": d, "account": account_of(d), "observed_at": _iso(now)})
    return r


# ------------------------------------------------------------------ cache

def _cache_path():
    return os.path.join(STATE_DIR, "capacity.json")


def load_cache():
    try:
        with open(_cache_path()) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_cache(c):
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    tmp = f"{_cache_path()}.{os.getpid()}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(c, fh, indent=1)
    os.replace(tmp, _cache_path())


def _stale(snap, now):
    """A reading is good for PROBE_EVERY_MIN minutes; a failed probe (no reading) is retried next time."""
    t = _ts(snap.get("observed_at") or "")
    return t is None or snap.get("logged_in") is None or now - t >= PROBE_EVERY_MIN * 60


# ----------------------------------------------------------------- status

def status(positions, dirs=None, now=None, fresh=False, probe=True, probe_fn=None, sessions_fn=live_sessions,
           emit=None, home=None):
    """One row per config dir: the cached reading, or a fresh probe when it is older than PROBE_EVERY_MIN
    minutes (or `fresh`); `probe=False` never probes. Live sessions and positions are always current.
    Every fresh reading is emitted as `of.capacity_snapshot`."""
    now = now or time.time()
    dirs = dirs if dirs is not None else config_dirs(home)
    probe_fn = probe_fn or globals()["probe"]
    emit = emit if emit is not None else (lambda kind, payload: of_events.emit(kind, payload))
    cache = load_cache()
    sessions = {d: sessions_fn(d) for d in dirs}
    by_dir = positions_by_dir(positions, sessions)
    rows, changed = [], False
    for d in dirs:
        snap = cache.get(d)
        probed = False
        if probe and (fresh or snap is None or _stale(snap, now)):
            snap = probe_fn(d, now)
            cache[d] = snap
            changed, probed = True, True
        row = dict(snap or {"config_dir": d, "account": account_of(d, home), "logged_in": None, "windows": {},
                            "observed_at": None, "probe": {}, "error": "not probed yet"})
        row.update(by_dir.get(d) or {"positions": [], "others": [], "sessions": 0, "busy": 0})
        rows.append(row)
        if probed and snap and snap.get("logged_in"):  # the week's growth calibrates it (calibrate_week)
            wk = (snap.get("windows") or {}).get("seven_day") or {}
            h = (cache.setdefault("_week_hist", {})).setdefault(d, [])
            h.append([now, wk.get("utilization"), wk.get("resets_at")])
            h[:] = [x for x in h if x[0] >= now - WEEK_HIST_KEEP_S]
        if probed:
            emit("capacity_snapshot", {k: row.get(k) for k in (
                "config_dir", "account", "logged_in", "status", "overage", "windows", "probe", "error",
                "observed_at", "positions", "others", "sessions", "busy")})
    if changed:
        save_cache(cache)
    return rows


def headroom(row):
    """The free share of the tightest window, in percent; None when the account gives no reading."""
    if not row.get("logged_in") or not row.get("windows"):
        return None
    vals = [100 - (w.get("utilization") or 0) for w in row["windows"].values() if w.get("utilization") is not None]
    return min(vals) if vals else None


def best_target(rows, exclude=None, ok=None):
    """The logged-in account with the most headroom, other than `exclude` (a config dir), among those `ok(dir)`
    accepts (default: all)."""
    cands = [(headroom(r), r) for r in rows if r.get("config_dir") != exclude and headroom(r) is not None
             and (ok is None or ok(r["config_dir"]))]
    if not cands:
        return None
    cands.sort(key=lambda x: (-x[0], (x[1].get("windows") or {}).get("seven_day", {}).get("utilization") or 0))
    return cands[0][1]


def over_threshold(row, thresholds=THRESHOLDS):
    """'<window> <util>% ≥ <threshold>%' for the first window past its threshold, else None."""
    if not row.get("logged_in"):
        return None
    for w in ("seven_day", "five_hour"):
        u = ((row.get("windows") or {}).get(w) or {}).get("utilization")
        if u is not None and u >= thresholds[w]:
            return f"{w} {u}% ≥ {thresholds[w]}%"
    return None


def _local(iso, with_date=False):
    t = _ts(iso or "")
    if t is None:
        return "—"
    return time.strftime("%d.%m %H:%M" if with_date else "%H:%M", time.localtime(t))


def _pct_text(u):
    return "—" if u is None else f"{u}%"


def format_table(rows, home=None):
    now = time.time()
    head = f"{'config dir':<18} {'account':<36} {'5h':>4} {'7d':>4}  {'resets 5h / 7d':<19} positions"
    out, detail = [head], []
    for r in rows:
        w = r.get("windows") or {}
        if r.get("logged_in"):
            five = _pct_text(w.get("five_hour", {}).get("utilization"))
            seven = _pct_text(w.get("seven_day", {}).get("utilization"))
            resets = f"{_local(w.get('five_hour', {}).get('resets_at'))} / {_local(w.get('seven_day', {}).get('resets_at'), True)}"
        else:
            five = seven = "—"
            resets = "not logged in" if r.get("logged_in") is False else f"no reading: {(r.get('error') or '—')[:40]}"
        names, by_org = r.get("positions") or [], r.get("by_org") or {}
        if len(names) <= 4 or not by_org:
            who = ", ".join(names) or "-"
        else:
            who = ", ".join(f"{org} {len(ns)}" for org, ns in sorted(by_org.items()))
            for org, ns in sorted(by_org.items()):
                detail.append(f"  {short(r['config_dir'], home)} {org}: {', '.join(ns)}")
        if r.get("others"):
            who += f"  +{len(r['others'])} other session(s)"
        if r.get("busy"):
            who += f"  ({r['busy']} busy)"
        out.append(f"{short(r['config_dir'], home):<18} {(r.get('account') or '?'):<36} {five:>4} {seven:>4}  {resets:<19} {who}")
    out.extend(detail)
    obs = [r.get("observed_at") for r in rows if r.get("observed_at")]
    if obs:
        t = _ts(min(obs))
        age = int((now - t) // 60) if t else None
        cost = sum((r.get("probe") or {}).get("cost_usd") or 0 for r in rows)
        out.append(f"probed {_local(min(obs))} local ({age} min ago); next probe after {PROBE_EVERY_MIN} min, "
                   f"`--fresh` now; last probes cost ${cost:.4f}")
    return "\n".join(out)


# ------------------------------------------------------------------- move
#
# A Workplace change with the same holder (docs/capacity.md): between turns, carry the session's
# files to the target config dir, merge memory three-way, resume in the same herdr pane under the
# canonical name, record `of.holder_changed`. The pure pieces are below; `move()` wires them.

import re as _re
import shutil

ANSI = _re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TRUST_DIALOG = _re.compile(r"Is this a project you created or one you trust|Yes, I trust this folder")
BYPASS_DIALOG = _re.compile(r"running in Bypass Permissions mode|Yes, I accept")
DIALOG_MARK = _re.compile(r"Enter to (select|confirm)|Esc to cancel|Ready to submit your answers|What do you want to do\?"
                          r"|enter confirm · esc|We recommend compacting earlier")  # the compaction offer, 2026-11-10
DIM = ("\x1b[2m", "38;2;153;153;153", "38;2;136;136;136", "38;5;8m", "\x1b[90m")


def find_transcript(d, sid):
    hits = glob.glob(os.path.join(d, "projects", "*", f"{sid}.jsonl"))
    return hits[0] if hits else None


def session_paths(d, sid):
    """Relative paths of one session's state in a config dir, those that exist (docs/capacity.md §move)."""
    t = find_transcript(d, sid)
    if not t:
        return []
    key = os.path.basename(os.path.dirname(t))
    cands = [f"projects/{key}/{sid}.jsonl", f"projects/{key}/{sid}", f"file-history/{sid}", f"session-env/{sid}",
             f"tasks/{sid}", f"todos/{sid}"]
    return [c for c in cands if os.path.exists(os.path.join(d, c))]


def project_key(d, sid):
    t = find_transcript(d, sid)
    return os.path.basename(os.path.dirname(t)) if t else None


def copy_session(src, dst, sid, backup, dry_run=False):
    """Copy the session's files; anything already at the target is first saved under `backup`."""
    acts = []
    for rel in session_paths(src, sid):
        s, t = os.path.join(src, rel), os.path.join(dst, rel)
        if os.path.exists(t):
            acts.append(("backup", rel))
            if not dry_run:
                b = os.path.join(backup, rel)
                os.makedirs(os.path.dirname(b), exist_ok=True)
                (shutil.copytree if os.path.isdir(t) else shutil.copy2)(t, b)
        acts.append(("copy", rel))
        if not dry_run:
            os.makedirs(os.path.dirname(t), exist_ok=True)
            if os.path.isdir(s):
                shutil.copytree(s, t, dirs_exist_ok=True)
            else:
                shutil.copy2(s, t)
    return acts


def _read(p):
    try:
        with open(p, "rb") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as fh:
        fh.write(data)


def merge_index(dst, src):
    """MEMORY.md: the target's lines, then the source's lines the target lacks (an index merges by union)."""
    have = set(dst.decode().splitlines())
    extra = [ln for ln in src.decode().splitlines() if ln.strip() and ln not in have]
    out = dst.decode().rstrip("\n")
    return ((out + "\n" + "\n".join(extra)) if extra else out).encode() + b"\n"


def _split_front(text):
    """(frontmatter, body) of a memory file: the frontmatter is the leading '---' block, or ''."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[:end + 5], text[end + 5:]
    return "", text


def merge3(ours, base, theirs):
    """Three-way line merge of a memory file that never drops a line: where both sides changed the same place,
    both versions are kept (git merge-file --union). The frontmatter is taken from the side that differs from
    the base (ours if both do), so a 'modified:' line is never doubled. 2026-11-07: eight shared Acme
    memory files changed on two accounts at once; "keep the target" left the other side's notes aside."""
    import tempfile
    if isinstance(ours, bytes):
        dec = lambda x: x.decode("utf-8", "surrogateescape") if isinstance(x, bytes) else x  # noqa: E731
        return merge3(dec(ours), dec(base), dec(theirs)).encode("utf-8", "surrogateescape")
    fo, bo = _split_front(ours)
    fb, bb = _split_front(base or "")
    ft, bt = _split_front(theirs)
    front = fo if fo != fb or ft == fb else ft
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for n, t in (("ours", bo), ("base", bb), ("theirs", bt)):
            paths.append(os.path.join(d, n))
            with open(paths[-1], "w") as fh:
                fh.write(t)
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        out = subprocess.run(["git", "merge-file", "-p", "--union", *paths], capture_output=True, text=True, env=env)
    if out.returncode < 0 or (out.returncode > 0 and not out.stdout):
        raise RuntimeError(f"git merge-file failed: {out.stderr.strip()}")
    return front + out.stdout


def merge_memory(src, dst, base, conflicts, dry_run=False):
    """Three-way merge of one memory directory into another; never loses an edit.
    `base` holds the version last carried (per file). For each source file:
      same on both sides          -> nothing
      missing at the target       -> copy
      only the source changed     -> copy (the target still equals the base)
      only the target changed     -> keep the target
      no base, the target older   -> the source; the older target saved under `conflicts` (*.older-target)
      both changed, MEMORY.md     -> union of lines
      both changed, other file    -> a three-way line merge from the base, keeping both sides (merge3); the
                                     source is also saved under `conflicts`. With no base: keep the target and
                                     save the source there, to merge by hand
    Returns [(file, action)]."""
    acts = []
    if not os.path.isdir(src):
        return acts
    for name in sorted(os.listdir(src)):
        sp = os.path.join(src, name)
        if not os.path.isfile(sp):
            continue
        s, t, b = _read(sp), _read(os.path.join(dst, name)), _read(os.path.join(base, name))
        if t == s:
            act = "same"
        elif t is None:
            act = "copy"
        elif b is not None and t == b:
            act = "update"
        elif b is not None and s == b:
            act = "keep"
        elif b is None and os.path.getmtime(os.path.join(dst, name)) <= os.path.getmtime(sp):
            act = "replace_older"  # no base: the target is an older copy (an account left earlier); kept aside
        elif name == "MEMORY.md":
            act = "union"
        elif b is not None:
            act = "merge3"
        else:
            act = "conflict"
        acts.append((name, act))
        if dry_run or act in ("same", "keep"):
            if not dry_run and act == "same":
                _write(os.path.join(base, name), s)
            continue
        if act == "replace_older":
            _write(os.path.join(conflicts, name + ".older-target"), t)
        if act in ("copy", "update", "replace_older"):
            _write(os.path.join(dst, name), s)
            _write(os.path.join(base, name), s)
        elif act == "union":
            _write(os.path.join(dst, name), merge_index(t, s))
            _write(os.path.join(base, name), s)
        elif act == "merge3":
            merged = merge3(t, b, s)
            _write(os.path.join(conflicts, name), s)
            _write(os.path.join(dst, name), merged)
            _write(os.path.join(base, name), merged)
        else:
            _write(os.path.join(conflicts, name), s)
    return acts


# --------------------------------------------------------------- a position's memory (RFC 0006)
#
# Each position's auto-memory lives outside the accounts, in ~/.local/state/of/memory/<org>/<position>/, and its
# sessions start with --settings '{"autoMemoryDirectory": …}'. A move then carries nothing: every account reads the
# same directory. The directory is seeded from the folder memories of every account: the files whose
# originSessionId is one of the position's sessions, merged as a move merges them, never dropping a line. Files of
# no known position stay in the folder for the structure keeper. A move or resume starts on the directory when
# capacity.yaml says `position_memory: true` (after of_lab's interactive check) or when the session already ran on it.

MEMORY_ROOT = os.path.join(STATE_DIR, "memory")
# The directory and its scope come from bin/of_memory.py (of_platform, RFC 0006 step 1): one resolver for of spawn
# and of capacity. A moved session keeps the directory it ran on, whatever capacity.yaml says.
_ORIGIN = _re.compile(r"^\s*originSessionId:\s*['\"]?([\w-]{1,64})", _re.M)
_MODIFIED = _re.compile(r"^\s*modified:\s*['\"]?(\S+?)['\"]?\s*$", _re.M)
_LINK = _re.compile(r"\]\(([^)\s]+\.md)\)")


def session_owners(rows, positions):
    """{session: (org, position)} from (ts, session, org, position) rows of the event store, the registry's
    holder.session winning. A session whose position was renamed answers with the name its last event used,
    resolved through aliases to the canonical one; a name no registry knows is dropped (its files stay unattributed)."""
    canon = {}
    for p in positions:
        for n in [p["name"]] + [str(a) for a in p.get("aliases") or []]:
            canon.setdefault((p.get("_org"), n), (p.get("_org"), p["name"]))
            canon.setdefault((None, n), (p.get("_org"), p["name"]))
    out = {}
    for _, sid, org, name in sorted((r for r in rows if r[1] and r[3]), key=lambda r: str(r[0] or "")):
        hit = canon.get((org, name)) or canon.get((None, name))
        if hit:
            out[sid] = hit
    for p in positions:
        sid = ((p.get("holder") or {}).get("session") or "").strip()
        if sid:
            out[sid] = (p.get("_org"), p["name"])
    return out


def _session_rows():
    """(ts, session, org, position) from the event store: hook events carry both, of.holder_changed names both
    ends of every move."""
    db = of_events._conn()
    import of_spend  # its view of the (session, position) pairs, kept by a cursor; the log itself only before it
    rows = [(last, sid, org, pos) for sid, org, pos, _, _, last in of_spend.owner_groups(db) if sid and pos]
    for ts, pl in db.execute("SELECT ts, payload FROM event WHERE name = 'of.holder_changed'"):
        try:
            j = json.loads(pl or "{}")
        except ValueError:
            continue
        for k in ("previous_session", "new_session"):
            if j.get(k) and j.get("position"):
                rows.append((ts, j[k], None, j["position"]))
    return rows


def folder_memories(dirs):
    """[(config dir, folder key, memory dir)] of every account."""
    out = []
    for d in dirs:
        for m in sorted(glob.glob(os.path.join(d, "projects", "*", "memory"))):
            out.append((d, os.path.basename(os.path.dirname(m)), m))
    return out


_MEM_PATH = r"(\S*/projects/[^/\s'\"]+/memory/[^\s'\"/;|&)]+\.md)"
_MEM_SHELL_WRITE = _re.compile(r"(>>?|\btee(?:\s+-a)?|\bsed\s+-i(?:\s+''|\s+\S+)*?)\s*['\"]?" + _MEM_PATH)


def memory_writes(dirs):
    """{<folder key>/memory/<file>: [(ts, session, tool)]}: every Write, Edit or shell write (>, >>, tee, sed -i)
    into a folder memory file, from the largest copy of each session's transcript (subagents' under their parent).
    Bulk copies (cp, rsync: the old account syncs, of capacity move) are not authorship and are not counted.
    2026-11-09: 1,495 of 2,098 such writes were shell heredocs, not Write."""
    best = {}
    for d in dirs:
        for f in glob.glob(os.path.join(d, "projects", "*", "*.jsonl")) + \
                glob.glob(os.path.join(d, "projects", "*", "*", "subagents", "*.jsonl")):
            sub = os.sep + "subagents" + os.sep in f
            sid = os.path.basename(os.path.dirname(os.path.dirname(f))) if sub else os.path.basename(f)[:-6]
            k = (sid, os.path.basename(f) if sub else "")
            sz = _size(f)
            if k not in best or sz > best[k][0]:
                best[k] = (sz, f, sid)
    out = {}
    for _, f, sid in best.values():
        try:
            fh = open(f, "rb")
        except OSError:
            continue
        with fh:
            for ln in fh:
                if b"/memory/" not in ln or b"tool_use" not in ln:
                    continue
                try:
                    j = json.loads(ln)
                except ValueError:
                    continue
                content = (j.get("message") or {}).get("content") if j.get("type") == "assistant" else None
                for c in content if isinstance(content, list) else []:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    inp = c.get("input") or {}
                    if c.get("name") in ("Write", "Edit", "MultiEdit"):
                        paths = [inp.get("file_path") or ""]
                    elif c.get("name") == "Bash":
                        paths = [m.group(2) for m in _MEM_SHELL_WRITE.finditer(inp.get("command") or "")]
                    else:
                        continue
                    for path in paths:
                        if "/projects/" in path and "/memory/" in path and path.endswith(".md"):
                            out.setdefault(path.split("/projects/", 1)[1], []).append(
                                (j.get("timestamp") or "", sid, c["name"]))
    return out


def _size(f):
    try:
        return os.path.getsize(f)
    except OSError:
        return 0


def attribute_memory(dirs, owners, writes=None):
    """({(org, position): {file: [(config dir, folder key, path)]}}, [unattributed paths]): every folder memory
    file but the index, by the owner of its originSessionId; failing that, with `writes` (memory_writes), by
    the last of its writers whose session has a position (of_architect, 2026-11-09: 742 files had no
    originSessionId)."""
    by, loose = {}, []
    for d, key, m in folder_memories(dirs):
        for f in sorted(os.listdir(m)):
            path = os.path.join(m, f)
            if f == "MEMORY.md" or not f.endswith(".md") or not os.path.isfile(path):
                continue
            head = (_read(path) or b"")[:4000].decode("utf-8", "replace")
            o = _ORIGIN.search(_split_front(head)[0] or head[:600])
            own = owners.get(o.group(1)) if o else None
            if not own and writes:
                known = [w for w in sorted(writes.get(f"{key}/memory/{f}") or []) if owners.get(w[1])]
                own = owners.get(known[-1][1]) if known else None
            if own:
                by.setdefault(own, {}).setdefault(f, []).append((d, key, path))
            else:
                loose.append(path)
    return by, loose


def union2(a, b):
    """Two copies with no common base, merged without dropping a line: the shared lines once, where they differ
    both sides (a's then b's). The frontmatter is the copy whose `modified:` is later (b's on a tie)."""
    import difflib
    dec = lambda x: x.decode("utf-8", "surrogateescape")  # noqa: E731
    fa, ba = _split_front(dec(a))
    fb, bb = _split_front(dec(b))
    ma, mb = (_MODIFIED.search(f) for f in (fa, fb))
    front = fa if (ma and mb and ma.group(1) > mb.group(1)) or (fa and not fb) else fb
    la, lb = ba.splitlines(keepends=True), bb.splitlines(keepends=True)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, la, lb, autojunk=False).get_opcodes():
        out += la[i1:i2]
        if op in ("replace", "insert"):
            out += lb[j1:j2]
    body = "".join(out)
    return (front + body).encode("utf-8", "surrogateescape")


def merge_copy(t, b, s):
    """(merged, action) of one source copy `s` into the position's copy `t`, `b` the source copy last taken."""
    if t is None:
        return s, "copy"
    if s == t:
        return t, "same"
    if b is not None and s == b:
        return t, "keep"
    if b is not None and t == b:
        return s, "update"
    if b is not None:
        return merge3(t, b, s), "merge3"
    return union2(t, s), "union"


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def _modified(data, path):
    m = _MODIFIED.search(_split_front((data or b"").decode("utf-8", "replace"))[0])
    if m:
        return m.group(1)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(_mtime(path)))


def build_index(mdir, known_lines):
    """The position's MEMORY.md: one line per file, newest first (frontmatter `modified:`, else the file's time).
    A file keeps the line its own index had, else a folder index's line, else one made from its name and
    description. Lines that name no file stay after the list."""
    cur = (_read(os.path.join(mdir, "MEMORY.md")) or b"").decode("utf-8", "replace").splitlines()
    own, rest = {}, []
    for ln in cur:
        m = _LINK.search(ln)
        if m and os.path.basename(m.group(1)) not in own:
            own[os.path.basename(m.group(1))] = ln
        elif ln.strip() and not m:
            rest.append(ln)
    files = [f for f in os.listdir(mdir) if f.endswith(".md") and f != "MEMORY.md"]
    stamped = []
    for f in files:
        data = _read(os.path.join(mdir, f))
        line = own.get(f) or known_lines.get(f)
        if not line:
            fm = _split_front((data or b"").decode("utf-8", "replace"))[0]
            nm = _re.search(r"^name:\s*(.+)$", fm, _re.M)
            ds = _re.search(r"^description:\s*['\"]?(.*?)['\"]?\s*$", fm, _re.M)
            line = f"- [{(nm.group(1).strip() if nm else f[:-3])}]({f})" + (f" — {ds.group(1)}" if ds else "")
        stamped.append((_modified(data, os.path.join(mdir, f)), f, line))
    lines = [x[2] for x in sorted(stamped, key=lambda x: (x[0], x[1]), reverse=True)] + rest
    return ("\n".join(lines) + "\n").encode() if lines else b""


def seed_position_memory(mdir, files, dirs, base_root=None, dry_run=False):
    """`mdir` is the scope's directory (of_memory.directory). Merge its attributed folder files (`files`: {file: [(config dir, key, path)]}) into its directory,
    oldest copy first, each against the copy last taken from that account and folder. Rebuilds the index when
    a file changed or it is missing. Returns [(file, action)] for the files that changed."""
    import of_memory
    base_root = base_root or os.path.join(STATE_DIR, "capacity", "memory-base", "positions")
    bkey = os.path.relpath(mdir, os.path.dirname(os.path.dirname(mdir)))  # <org>/<scope id>
    acts = []
    for f, copies in sorted(files.items()):
        t = _read(os.path.join(mdir, f))
        changed, last = False, None
        for d, key, path in sorted(copies, key=lambda c: _mtime(c[2])):
            bpath = os.path.join(base_root, bkey, short(d).strip("~/") or "default", key, f)
            s = _read(path)
            if s is None:
                continue
            t2, act = merge_copy(t, _read(bpath), s)
            if act not in ("same", "keep"):  # report the strongest: union > merge3 > update > copy
                changed = True
                last = max(last or act, act, key=["copy", "update", "merge3", "union"].index)
            t = t2
            if not dry_run:
                _write(bpath, s)
        if changed:
            acts.append((f, last))
            if not dry_run:
                of_memory.ensure(mdir)
                _write(os.path.join(mdir, f), t)
                # the newest copy's time, not the seeding's: a file with no `modified:` sorts by it in the index
                newest = max((_mtime(p) for _, _, p in copies), default=0)
                if newest:
                    os.utime(os.path.join(mdir, f), (newest, newest))
    if not dry_run and (acts or (os.path.isdir(mdir) and not os.path.exists(os.path.join(mdir, "MEMORY.md")))):
        known = {}
        for d, key, m in sorted(folder_memories(dirs), key=lambda x: _mtime(os.path.join(x[2], "MEMORY.md"))):
            for ln in (_read(os.path.join(m, "MEMORY.md")) or b"").decode("utf-8", "replace").splitlines():
                lm = _LINK.search(ln)
                if lm and os.path.basename(lm.group(1)) in files:
                    known[os.path.basename(lm.group(1))] = ln  # the newest folder index wins
        of_memory.ensure(mdir)
        _write(os.path.join(mdir, "MEMORY.md"), build_index(mdir, known))
    return acts


def seed_memory(positions, only=None, extra_sessions=None, dirs=None, root=None, dry_run=False, log=print,
                transcripts=False):
    """Seed every memory directory (or only the one of position `only` = (org, name)) from the accounts' folder
    memories (RFC 0006 §2.2), each position's files into its scope's directory (of_memory.directory).
    `extra_sessions` {session: (org, name)} adds sessions the store does not know yet. Returns {"positions":
    {dir: [(file, action)]}, "owners": {dir: {file: copies}}, "members": {dir: [names]}, "unattributed": [paths]}."""
    import of_memory
    dirs = dirs if dirs is not None else config_dirs()
    owners = session_owners(_session_rows(), positions)
    owners.update(extra_sessions or {})
    writes = memory_writes(dirs) if transcripts else None
    by, loose = attribute_memory(dirs, owners, writes)
    pos = {(p.get("_org"), p["name"]): p for p in positions}
    scoped, members = {}, {}
    for own, files in by.items():
        mdir = of_memory.directory(pos.get(own) or {"name": own[1], "_org": own[0]}, root)
        members.setdefault(mdir, []).append(own[1])
        for f, copies in files.items():
            scoped.setdefault(mdir, {}).setdefault(f, []).extend(copies)
    want = of_memory.directory(pos.get(only) or {"name": only[1], "_org": only[0]}, root) if only else None
    out = {}
    for mdir, files in sorted(scoped.items()):
        if want and (mdir != want or only[1] not in members[mdir]):
            continue
        out[mdir] = seed_position_memory(mdir, files, dirs, dry_run=dry_run)
    return {"positions": out, "owners": scoped, "members": members, "unattributed": loose, "writes": writes,
            "sessions": owners}


_TYPE_ORDER = ("feedback", "project", "reference", "user")


def _session_hint(dirs, sid):
    """(cwd, first prompt) of a session with no position, for the keeper who decides whose lessons they are."""
    best = max((f for d in dirs for f in glob.glob(os.path.join(d, "projects", "*", f"{sid}.jsonl"))),
               key=_size, default=None)
    if not best:
        return None, None
    cwd = first = None
    with open(best, "rb") as fh:
        for n, ln in enumerate(fh):
            if n > 400 or (cwd and first):
                break
            try:
                j = json.loads(ln)
            except ValueError:
                continue
            cwd = cwd or j.get("cwd")
            m = j.get("message") or {}
            if not first and j.get("type") == "user" and isinstance(m.get("content"), str):
                first = " ".join(m["content"].split())[:160]
    return cwd, first


def _org_paths():
    """[(folder key prefix, org)] from orgs.yaml `paths`, longest first (Claude Code's key: the path, / and . as -)."""
    out = []
    try:
        import yaml
        with open(of_events.config_path("orgs.yaml")) as fh:
            doc = yaml.safe_load(fh) or {}
    except Exception:
        return out
    for o in doc.get("orgs") or []:
        for pth in (o.get("paths") or []) + ([o["path"]] if o.get("path") else []):
            out.append((_re.sub(r"[/.]", "-", os.path.expanduser(pth)), o.get("name")))
    return sorted(out, key=lambda x: -len(x[0]))


def _org_of_folder(key, org_paths):
    return next((o for pre, o in org_paths if key == pre or key.startswith(pre + "-")), None)


def leftover_memory(loose, writes, owners, dirs, org_paths=None):
    """{org or None: [row]} of the files no position took, one row per file name and folder (its newest copy),
    feedback first: {type, file, folder, name, description, path, copies, writer, hint}. The org is the one of
    any position that ever wrote it, else the organization whose checkout holds the folder (None: unknown)."""
    groups = {}
    for path in loose:
        groups.setdefault(os.path.basename(path), []).append(path)
    hints, rows = {}, {}
    org_paths = _org_paths() if org_paths is None else org_paths
    for fname, paths in groups.items():
        path = max(paths, key=_mtime)
        rel = path.split("/projects/", 1)[1]
        folders = sorted({p.split("/projects/", 1)[1].split("/memory/")[0] for p in paths})
        fm = _split_front(((_read(path) or b"")[:4000]).decode("utf-8", "replace"))[0]
        typ = _re.search(r"^\s*type:\s*['\"]?(\w+)", fm, _re.M)
        nm = _re.search(r"^name:\s*(.+)$", fm, _re.M)
        ds = _re.search(r"^description:\s*['\"]?(.*?)['\"]?\s*$", fm, _re.M)
        ws = sorted(w for p in paths for w in (writes or {}).get(p.split("/projects/", 1)[1]) or [])
        org = next((owners[w[1]][0] for w in reversed(ws) if owners.get(w[1])), None) or \
            _org_of_folder(rel.split("/memory/")[0], org_paths)
        writer = ws[-1][1] if ws else None
        if writer and writer not in hints:
            hints[writer] = _session_hint(dirs, writer)
        rows.setdefault(org, []).append({
            "type": typ.group(1) if typ else None, "file": fname, "folder": rel.split("/memory/")[0], "folders": folders,
            "name": nm.group(1).strip() if nm else None, "description": ds.group(1) if ds else None, "path": path,
            "copies": len(paths), "writer": writer, "hint": hints.get(writer)})
    rank = lambda r: (_TYPE_ORDER.index(r["type"]) if r["type"] in _TYPE_ORDER else 9, r["folder"], r["file"])  # noqa: E731
    return {o: sorted(rs, key=rank) for o, rs in rows.items()}


def write_leftover_report(org, rows, root=None, now=None, dry_run=False):
    """The structure keeper's list of folder memory files no position took (RFC 0006 §2.2), feedback first, in
    the organization's memory directory (mode 700, never committed: memory can hold what a repository must not)."""
    import of_memory
    d = os.path.join(root or of_memory.root(), org or "_")
    path = os.path.join(d, "_unattributed.md")
    if dry_run:
        return path
    home = os.path.expanduser("~")
    sh = lambda x: ("~" + x[len(home):]) if x and x.startswith(home) else x  # noqa: E731
    out = [f"# Folder memory no position took ({org or 'no organization'})", "",
           f"Written by `of capacity memory --from-transcripts --report` on {_iso(now or time.time())[:16]}Z "
           "(RFC 0006 §2.2). Each file is in a folder memory, by its originSessionId or its writers no position "
           "could be named. Decide per file: a rule goes to a brief or a rules file, a position's lesson goes to "
           "its memory directory, the rest stays or is deleted. Feedback first.", ""]
    for typ in list(_TYPE_ORDER) + [None]:
        part = [r for r in rows if r["type"] == typ or (typ is None and r["type"] not in _TYPE_ORDER)]
        if not part:
            continue
        out += [f"## {typ or 'no type'} ({len(part)})", ""]
        for r in part:
            where = r["folder"] if len(r.get("folders") or []) <= 1 else f"{len(r['folders'])} folders"
            line = f"- `{r['file']}` ({where}, {r['copies']} cop{'y' if r['copies'] == 1 else 'ies'}): " \
                   f"{r['description'] or r['name'] or 'no description'}. Newest: {sh(r['path'])}."
            if r["writer"]:
                cwd, first = r["hint"] or (None, None)
                line += f" Written by session {r['writer'][:8]}" + (f" in {sh(cwd)}" if cwd else "") + \
                        (f", first task: \"{first}\"" if first else "") + "."
            out.append(line)
        out.append("")
    of_memory.ensure(d)
    _write(path, "\n".join(out).encode())
    return path



def input_draft(ansi_lines):
    """The text typed into Claude Code's input box, or '' (a dim suggestion is not a draft)."""
    plain_all = [ANSI.sub("", x) for x in ansi_lines]
    if any(DIALOG_MARK.search(x) for x in plain_all[-25:]):
        return None  # a menu or question dialog is open: there is no input box to type into
    start, marks = None, [i for i, x in enumerate(plain_all) if x.lstrip().startswith("❯")]

    def boxed(i):  # the input box sits right under a horizontal rule; history and agent lists do not
        j = i - 1
        while j >= 0 and not plain_all[j].strip():
            j -= 1
        return j >= 0 and plain_all[j].strip().startswith("─")
    boxes = [i for i in marks if boxed(i)]
    if boxes:
        start = boxes[-1]
    elif marks:
        start = marks[-1]  # the box is the last prompt mark; earlier ones are history
    if start is None:
        return None  # no input box on screen: not at an agent prompt
    if _re.match(r"^\s*❯\s*\d+\.\s", plain_all[start]):
        return None  # "❯ 1. …" is a selected menu option, never the input box
    out = []
    for ln in ansi_lines[start:]:
        plain = ANSI.sub("", ln)
        if plain.strip().startswith("─") and out:
            break
        body = plain.replace("❯", "", 1).replace("\xa0", " ").strip() if not out else plain.strip()
        if body and not any(m in ln for m in DIM):
            out.append(body)
        elif not out:
            out.append("")
    return "\n".join(x for x in out if x).strip()


def proc_env(pid):
    """Selected environment of a running process (macOS/Linux `ps eww`): Zellij and herdr placement."""
    try:
        out = subprocess.run(["ps", "eww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return {}
    keep = ("ZELLIJ_PANE_ID", "ZELLIJ_SESSION_NAME", "HERDR_PANE_ID", "CLAUDE_CONFIG_DIR")
    return dict(t.split("=", 1) for t in out.split() if t.split("=", 1)[0] in keep and "=" in t)


def proc_args(pid):
    try:
        return subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return ""


def first_pane_id(result):
    """The first `pane_id` anywhere in a herdr JSON result (create/split answers differ in shape)."""
    if isinstance(result, dict):
        if isinstance(result.get("pane_id"), str):
            return result["pane_id"]
        for v in result.values():
            x = first_pane_id(v)
            if x:
                return x
    elif isinstance(result, list):
        for v in result:
            x = first_pane_id(v)
            if x:
                return x
    return None


def place_command(ws_label, tab_label, cwd, workspaces, tabs, panes):
    """Pure: the herdr command that opens a new shell pane in tab `tab_label` of workspace `ws_label`
    (creating either when missing). Returns (command, follow-up rename or None)."""
    ws = next((w for w in workspaces if w.get("label") == ws_label), None)
    if not ws:
        return ["workspace", "create", "--label", ws_label, "--cwd", cwd, "--no-focus"], ("tab", tab_label)
    tab = next((t for t in tabs if t["workspace_id"] == ws["workspace_id"] and t.get("label") == tab_label), None)
    if not tab:
        return ["tab", "create", "--workspace", ws["workspace_id"], "--label", tab_label, "--cwd", cwd, "--no-focus"], None
    ps = [x["pane_id"] for x in panes if x.get("tab_id") == tab["tab_id"]]
    split = "right" if len(ps) % 2 == 1 else "down"
    return ["pane", "split", ps[-1], "--direction", split, "--cwd", cwd, "--no-focus"], None


def launch_prompt(note_path, resumed=False, model=None, restarted=False):
    """The first prompt of a moved session: the text `_inject` declares and the command line carries.
    model: the new model, when the move changes it; restarted: a restart on the same account and model."""
    if restarted:
        return f"of_capacity restarted this session on the same account. Read {note_path} and follow it."
    if model:
        return f"of_capacity restarted this session on {model}. Read {note_path} and follow it."
    if resumed:
        return f"of_capacity resumed this session after a move that did not finish. Read {note_path} and follow it."
    return f"of_capacity moved this session to another account. Read {note_path} and follow it."


def launch_command(cwd, target, sid, name, note_path, model=None, effort=None, bypass=True, resumed=False,
                   memory_dir=None, prompt=None, deny=None, state_dir=None, tools=None):
    """memory_dir: the position's own memory (RFC 0006), the same on every account; deny: its credential deny rules
    (deny_for), so a moved or resumed session keeps what of spawn gave it. Both go into one settings file, as of spawn
    writes it (of_spawn.settings_file): a fresh pane's shell takes at most 1,024 bytes a line, and the deny rules
    alone can pass 4 KB (of_platform, 2026-11-11). tools: (argv, settings keys) of the position's tool profile
    (tools_for; docs/tools.md §4): its --mcp-config and its allowedMcpServers / enabledPlugins, in the same file."""
    env = ["env", "-u", "CLAUDE_CONFIG_DIR"] if is_default(target) else [f"CLAUDE_CONFIG_DIR={_q(target)}"]
    parts = ["cd", _q(cwd), "&&", *env, "claude", "--resume", sid, "-n", name]
    t_argv, t_settings = tools or ([], {})
    settings = dict(t_settings or {})
    if memory_dir:
        settings["autoMemoryDirectory"] = memory_dir
    if deny:
        settings["permissions"] = {"deny": list(deny)}
    if settings:
        import of_spawn
        parts += ["--settings", _q(of_spawn.settings_file(json.dumps(settings), sid, state_dir))]
    parts += [_q(str(x)) for x in t_argv or []]
    if model:
        parts += ["--model", model]
    if effort:
        parts += ["--effort", effort]
    if bypass:
        parts.append("--dangerously-skip-permissions")
    parts.append(_q(prompt or launch_prompt(note_path, resumed)))
    return " ".join(parts)


def deny_for(p, log=print):
    """The position's credential deny rules, as of spawn writes them (docs/registry.md, Credentials; of_platform,
    2026-11-11). None without a position or with a broken catalogue: of registry check reports that, and the guard
    still applies."""
    if not p or not p.get("name"):
        return None
    try:
        import of_credentials
        import of_spawn
        return of_spawn.deny_rules(p["name"], p.get("_org"), of_credentials.load()) or None
    except Exception as e:  # noqa: BLE001
        log(f"credentials: no deny rules for {p['name']} ({type(e).__name__}: {e}); the guard still applies")
        return None


def _q(s):
    return s if _re.match(r"^[\w:./=@%+~-]+$", s) else "'" + s.replace("'", "'\\''") + "'"


def last_model(transcript):
    """The model of the session's last main-thread assistant message."""
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as fh:
            fh.seek(max(0, size - 2_000_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return None
    for ln in reversed(lines):
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        m = j.get("message")
        if j.get("type") == "assistant" and isinstance(m, dict) and m.get("model") and not j.get("isSidechain") \
                and not str(m.get("model")).startswith("<"):
            return m["model"]
    return None


def limit_stop(transcript, tail=262_144):
    """When the session stopped on its account's usage limit and has not had a reply since: the epoch of the
    harness's synthetic "You've hit your ... limit" message (isApiErrorMessage, apiError usage_limit_reached) when
    it is the last main-thread assistant record; else None. Observed in the session itself, minutes before a probe
    sees the account rejected (2026-11-10: stop 20:08:15Z, probe 20:10:20Z; probes run at most every 30 minutes)."""
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as fh:
            fh.seek(max(0, size - tail))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return None
    for ln in reversed(lines):
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        if j.get("type") != "assistant" or j.get("isSidechain"):
            continue
        if j.get("isApiErrorMessage") and (j.get("apiError") == "usage_limit_reached" or _re.search(
                r"hit your .{0,20}limit", json.dumps((j.get("message") or {}).get("content") or ""))):
            return _ts(j.get("timestamp") or "") or None
        return None
    return None


def last_field(transcript, key):
    """The last main-thread record's value of `key` (cwd, permissionMode) in a transcript."""
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as fh:
            fh.seek(max(0, size - 2_000_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return None
    for ln in reversed(lines):
        if f'"{key}"' not in ln:
            continue
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        if j.get(key) and not j.get("isSidechain"):
            return j[key]
    return None


def pending_question(transcript):
    """The session's last AskUserQuestion that has no tool result yet: (input, timestamp) or None."""
    asked, answered = None, set()
    try:
        with open(transcript, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                if "AskUserQuestion" not in ln and "tool_result" not in ln:
                    continue
                try:
                    j = json.loads(ln)
                except ValueError:
                    continue
                m = j.get("message")
                for c in (m.get("content") if isinstance(m, dict) and isinstance(m.get("content"), list) else []):
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_use" and c.get("name") == "AskUserQuestion":
                        asked = (c.get("id"), c.get("input"), j.get("timestamp"))
                    elif c.get("type") == "tool_result":
                        answered.add(c.get("tool_use_id"))
    except Exception:
        return None
    if asked and asked[0] not in answered:
        return asked[1], asked[2]
    return None


def background_processes(pid):
    """What runs under the harness process besides MCP servers: the commands its background shells run."""
    out = []
    try:
        kids = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True, timeout=10).stdout.split()
        for c in kids:
            a = proc_args(c)
            if not a or "mcp" in a.split()[0].lower() or "mcp-" in a or a.startswith("caffeinate "):
                continue  # MCP servers, and the harness's own keep-awake while a turn runs
            cmd = a.split("&& eval ", 1)[-1] if "&& eval " in a else a
            out.append(cmd.rsplit(" < /dev/null", 1)[0].strip("'")[:400])
    except Exception:
        pass
    return out


def question_note(q, ts):
    qs = (q or {}).get("questions") or []
    lines = [f"Your question to the founder (asked {ts}) was open when the move closed its dialog; it is lost "
             "unless you ask again. **Ask it again with `of ask --blocking`**, with its options in "
             "`--options \"a|b\"`, so it shows in his popup and in herdr. Re-check any time it names first."]
    for x in qs:
        lines.append(f"- {x.get('header') or 'question'}: {x.get('question')}")
        for o in x.get("options") or []:
            lines.append(f"  - option «{o.get('label')}»: {o.get('description') or ''}")
    return "\n".join(lines)


def effort_of(d, model):
    try:
        with open(os.path.join(d, "settings.json")) as fh:
            return ((json.load(fh).get("modelSettings") or {}).get(model) or {}).get("effortLevel")
    except Exception:
        return None


HOLDER_LAG_NOTE = ("If `of inbox --mine` or `of send` answers `403 not_holder` right after this, the relay server has "
                   "not seen your new process yet; it should within about 10 seconds (of_server, 2026-11-07), so retry in "
                   "10 to 20 seconds, do not investigate.")


def handover_note(name, sid, src, dst, account, continuation, ts, extra=None, model=None, why=None, restarted=False):
    """model: (old, new) when the move changes the session's model; why: the reason, said to the session;
    restarted: a restart on the same account and model (move --restart)."""
    if restarted:
        lines = [f"# Restart: {name}", "",
                 f"`of_capacity` restarted this session (`{sid}`) at {ts} on the same account, `{short(dst)}` "
                 f"({account}). " + (f"{why.strip()} " if why else "")
                 + "Same session, same pane, same name; your context, memory and files are carried, and your model, "
                   "effort and permissions are unchanged.", ""]
    elif model:
        moved = f" and moved it from `{short(src)}` to `{short(dst)}` ({account})" if src != dst else ""
        lines = [f"# Model change: {name}", "",
                 f"`of_capacity` restarted this session (`{sid}`) at {ts} on `{model[1]}` (was `{model[0]}`){moved}. "
                 + (f"{why.strip()} " if why else "")
                 + "Same session, same pane, same name; your context, memory and files are carried, and your effort "
                   "and permissions are unchanged.", ""]
    else:
        lines = [f"# Account move: {name}", "",
                 f"`of_capacity` moved this session (`{sid}`) at {ts} from `{short(src)}` to `{short(dst)}` "
                 f"({account}): the old account is near its model limits. Same session, same pane, same name; "
                 "your context, memory and files are carried. Nothing else changed.", ""]
    crons = (continuation or {}).get("session_crons") or []
    tasks = [t for t in (continuation or {}).get("background_tasks") or [] if t.get("status") == "running"]
    if crons or tasks:
        lines.append("Crons, monitors and background tasks died with the old process. Your last record of them:")
        for c in crons:
            lines.append(f"- cron/wake-up: {json.dumps(c, ensure_ascii=False)[:2000]}")
        for t in tasks:
            lines.append(f"- background {t.get('type')}: {t.get('description') or ''} "
                         f"({(t.get('command') or '')[:160]!r})")
        lines += ["Re-create the ones you still need.", ""]
    else:
        lines += ["Your last record shows no crons, monitors or background tasks.", ""]
    lines += ["Check that the move kept your context: reply in at most three lines with",
              "1. what you were working on before this message, and",
              "2. your next step.",
              "Then declare your state with `of status` as usual and carry on (or stay idle if you were waiting).",
              "", HOLDER_LAG_NOTE]
    if extra:
        lines += ["", "## From your organization", "", extra.strip()]
    return "\n".join(lines) + "\n"


def workplace_edit(text, name, account):
    """Set `account <dir>` in the position's holder.workplace line of a registry, keeping its formatting
    and its quoting. Returns (new text, changed); the text is unchanged unless the result is valid YAML
    (a lost closing quote once made a whole registry unreadable, 2026-11-06)."""
    lines = text.split("\n")
    in_pos, changed = False, False
    for i, ln in enumerate(lines):
        m = _re.match(r"^(\s*)- name:\s*(\S+)\s*$", ln)
        if m:
            in_pos = m.group(2) == name
            continue
        w = _re.match(r"^(\s+workplace:\s*)(.*?)\s*$", ln)
        if in_pos and w:
            head, value = w.group(1), w.group(2)
            q = value[0] if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) > 1 else ""
            inner = value[1:-1] if q else value
            inner = _re.sub(r",\s*account [^,]+$", "", inner)
            new = f"{head}{q}{inner}, account {account}{q}"
            changed = new != ln
            lines[i] = new
            break
    out = "\n".join(lines)
    if changed:
        try:
            import yaml
            yaml.safe_load(out)
        except Exception:
            return text, False
    return out, changed


def propose_registry(registry_file, name, account, workplace=None, out_dir=None):
    """The registry change a move implies, as a unified diff for the organization's structure keeper
    (single writer, of_architect 2026-11-06): `of capacity` never edits a registry. `workplace` replaces
    the holder.workplace value (a new runtime or pane). The account is runtime state, not structure (both
    registries dropped it on 2026-11-06): an account-only move implies no registry change. Returns
    (diff text, path written or None); empty diff when nothing changes or the result is not valid YAML."""
    import difflib
    with open(registry_file) as fh:
        text = fh.read()
    if workplace:
        lines, in_pos = text.split("\n"), False
        for i, ln in enumerate(lines):
            m = _re.match(r"^(\s*)- name:\s*(\S+)\s*$", ln)
            if m:
                in_pos = m.group(2) == name
                continue
            w = _re.match(r"^(\s+workplace:\s*)", ln)
            if in_pos and w:
                lines[i] = f"{w.group(1)}{json.dumps(workplace, ensure_ascii=False)}"
                break
        new = "\n".join(lines)
        try:
            import yaml
            yaml.safe_load(new)
        except Exception:
            new = text
    else:
        new = text  # the account is runtime state: nothing for the registry
    rel = "registry/positions.yaml" if registry_file.endswith("registry/positions.yaml") else os.path.basename(registry_file)
    diff = "".join(difflib.unified_diff(text.splitlines(True), new.splitlines(True), f"a/{rel}", f"b/{rel}"))
    path = None
    if diff and out_dir:
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "registry.diff")
        with open(path, "w") as fh:
            fh.write(diff)
    return diff, path


class MoveError(Exception):
    def __init__(self, msg, untrusted=None, stuck=None, cancelled_wait=False):
        super().__init__(msg)
        self.untrusted = untrusted  # a folder no config trusts: the owner decides (of ask), never the tool
        self.stuck = stuck  # a pane the tool could not put back as it was: someone must look at it now
        # the move pressed Esc on the session's limit wait and then failed: the session no longer continues by
        # itself when its window reopens, so the balancer tries again on its next run, not after FAILED_RETRY_S
        self.cancelled_wait = cancelled_wait


AFTER_ESC_READS = 5      # reads, 2 s apart, for the input box to come back after the limit wait is cancelled
FAILED_RETRY_S = 1800    # a failed move is not retried before this, except one that cancelled a limit wait


def failed_hold_s(e):
    """How long the balancer leaves a session alone after its move failed with MoveError `e`: not every pulse, but a
    session whose limit wait the move cancelled is tried again on the next run (it no longer continues by itself)."""
    return 6 * 3600 if e.untrusted else 0 if e.cancelled_wait else FAILED_RETRY_S


# ------------------------------------------------------------ folder trust
#
# Claude Code asks once per folder whether to trust it (projects[<path>].hasTrustDialogAccepted in the
# config's .claude.json). A resumed session in an untrusted folder sits on that dialog (runners_keeper,
# 2026-11-06). A git worktree takes the trust of its main repository. Rule (of_architect, 2026-11-06):
# before a move, check the target; carry the source's trust (the owner's earlier decision) and record it;
# if no config trusts the folder, do not move and ask the owner. Never press the dialog.

def _git(cwd, *args):
    try:
        out = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def trust_candidates(cwd):
    """The keys Claude Code may look up for a folder: the folder, its git top level, a worktree's main repo."""
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    main = os.path.dirname(common) if common.endswith("/.git") else ""
    out = []
    for k in (cwd, _git(cwd, "rev-parse", "--show-toplevel"), main):
        if k and k not in out:
            out.append(k)
    return out


def _projects(d):
    try:
        with open(account_file(d)) as fh:
            return json.load(fh).get("projects") or {}
    except Exception:
        return {}


def trusted_key(d, cwd, candidates=None):
    projects = _projects(d)
    for k in candidates if candidates is not None else trust_candidates(cwd):
        if isinstance(projects.get(k), dict) and projects[k].get("hasTrustDialogAccepted"):
            return k
    return None


# Settings that belong to the machine and the owner, not to the claude.ai account. A new account's config dir
# has none of them (2026-11-07, mail@acme.example): its resume stopped at the Bypass Permissions warning, and its
# sessions would have run without the OF hooks (the collector and the guard). As with folder trust, the owner's
# earlier decision on the source account is carried, never pressed in a dialog; existing values are never
# overwritten.
CARRIED_SETTINGS = ("hooks", "skipDangerousModePermissionPrompt", "permissions", "modelSettings", "autoCompactEnabled",
                    "autoCompactWindow", "cleanupPeriodDays", "language")


def ensure_settings(src, dst, dry_run=False):
    """Carry the CARRIED_SETTINGS keys the target's settings.json lacks; returns the keys carried."""
    def load(d):
        try:
            with open(os.path.join(d, "settings.json")) as fh:
                return json.load(fh)
        except FileNotFoundError:
            return {}
    a, b = load(src), load(dst)
    keys = [k for k in CARRIED_SETTINGS if k in a and k not in b]
    if keys and not dry_run:
        b.update({k: a[k] for k in keys})
        path = os.path.join(dst, "settings.json")
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(b, fh, indent=2)
        os.replace(tmp, path)
    return keys


# Agent sessions run in Bypass Permissions mode (the owner's choice: they start with --dangerously-skip-permissions).
# On 2026-11-07 warehouse_keeper was resumed onto the new mail@acme.example account, whose settings then lacked
# skipDangerousModePermissionPrompt, and ran a day in auto mode, Claude Code's default since 2.1.x, with the flag
# still on its command line; the exact path was not reconstructed. Founder, 2026-11-08: agent sessions start and
# stay in bypass. So every start checks the footer, and every balance run turns such a pane back.
MODE_FOOTER = _re.compile(r"(?:⏵⏵|⏸)\s*(bypass permissions|auto mode|accept edits|plan mode|don't ask)\s+on")
MODE_NAMES = {"bypass permissions": "bypass", "auto mode": "auto", "accept edits": "accept", "plan mode": "plan",
              "don't ask": "dontask"}
BYPASS_FLAG = "--dangerously-skip-permissions"


def perm_mode(lines):
    """The permission mode in Claude Code's footer: bypass, auto, accept, plan, dontask; 'default' when the input
    box is drawn with no mode line; None when the screen shows neither (a dialog, a shell)."""
    plain = [ANSI.sub("", x) for x in lines]
    for x in reversed(plain[-12:]):
        m = MODE_FOOTER.search(x)
        if m:
            return MODE_NAMES[m.group(1)]
    return "default" if input_draft(lines) is not None else None


def ensure_bypass(read, key, log=print, presses=6, pause=1.2, sleep=time.sleep):
    """Turn a session started with --dangerously-skip-permissions back to bypass: shift+tab cycles the modes, one
    press at a time, the footer re-read after each. Only with the input box drawn and empty (no dialog, no draft).
    Returns the mode it ends in ('bypass' on success), or None when the screen shows no mode."""
    lines = read()
    mode = perm_mode(lines)
    if mode in (None, "bypass"):
        return mode
    for _ in range(presses):
        draft = input_draft(lines)
        if draft is None or draft:
            log(f"  permission mode {mode}: not switched ({'a dialog is open' if draft is None else 'a draft'})")
            return mode
        key("shift+tab")
        sleep(pause)
        lines = read()
        was, mode = mode, perm_mode(lines)
        if mode == "bypass":
            return mode
        if mode is None or mode == was:  # the press showed nothing: stop rather than cycle blind
            log(f"  permission mode: shift+tab left it at {mode}; stopped")
            return mode
    log(f"  permission mode still {mode} after {presses} presses of shift+tab")
    return mode


def ran_in_bypass(transcript):
    """The session ran in Bypass Permissions mode at any point (its transcript records the mode per prompt)."""
    try:
        with open(transcript, "rb") as fh:
            return any(b'"bypassPermissions"' in ln for ln in fh)
    except OSError:
        return False


def check_bypass_after_start(pane, log=print, wait=20, step=2, herdr=None):
    """After a start with --dangerously-skip-permissions: the footer must say bypass; turn it back if not. Never
    fails the start: an error is logged, and the next balance run heals the pane. `herdr` as in _herdr (tests)."""
    herdr = herdr or _herdr

    def read():
        r = herdr("pane", "read", pane, "--source", "visible", "--lines", "40", "--format", "ansi")
        text = ((r or {}).get("read") or {}).get("text") if isinstance(r, dict) else r
        return (text or "").splitlines()
    try:
        end = time.time() + wait
        while True:
            mode = perm_mode(read())
            if mode is not None or time.time() >= end:
                break
            time.sleep(step)
        if mode in (None, "bypass"):
            return mode
        after = ensure_bypass(read, lambda k: herdr("pane", "send-keys", pane, k), log=log)
        log(f"  permission mode after start: {mode} -> {after}")
        return after
    except Exception as e:
        log(f"  permission mode after start: not checked ({e})")
        return None


MOVE_BACKUP_KEEP_H = 24


def prune_move_backups(live_names, now=None, root=None, keep_h=MOVE_BACKUP_KEEP_H, log=print, dry_run=False):
    """Delete the `backup/` of a move work dir once the move is a day old and its position runs in a live session.
    The backup holds the target's older copy of the session's files, saved before the copy; it is only needed to
    undo a move gone wrong. note.md, conflicts/ and registry.diff stay (kilobytes). of_platform, 2026-11-09: 193 dirs
    held 7.2 GB in two days, storefront_operator's three moves 0.8 GB each, with the Mac's disk at 60 GiB free.
    Returns [(dir, bytes freed)]."""
    import shutil
    now = now or time.time()
    root = root or os.path.join(STATE_DIR, "capacity", "moves")
    out = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        m = _re.match(r"^(?P<pos>.+)-(?P<ts>\d{8}T\d{6}Z)(?:-resume)?$", name)
        b = os.path.join(root, name, "backup")
        if not m or not os.path.isdir(b) or m.group("pos") not in live_names:
            continue
        ts = calendar.timegm(time.strptime(m.group("ts"), "%Y%m%dT%H%M%SZ"))
        if now - ts < keep_h * 3600:
            continue
        size = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(b) for f in fs
                   if not os.path.islink(os.path.join(dp, f)))
        if not dry_run:
            shutil.rmtree(b, ignore_errors=True)
        out.append((name, size))
    if out:
        log(f"move backups: {'would free' if dry_run else 'freed'} {sum(x[1] for x in out) / 1e9:.1f} GB "
            f"in {len(out)} dir(s) older than {keep_h} h")
    return out


def heal_permission_modes(log=print, dirs=None, dry_run=False):
    """Every idle Claude Code pane whose process was started with --dangerously-skip-permissions but shows another
    mode is turned back to bypass. Runs with each balance. Returns [(pane, session, before, after)]."""
    dirs = dirs or config_dirs()
    pid_of = {s["session"]: s["pid"] for d in dirs for s in live_sessions(d)}
    out = []
    try:
        panes = _herdr("pane", "list")["panes"]
    except Exception as e:
        log(f"permission modes: herdr pane list failed: {e}")
        return out
    for p in panes:
        sid = (p.get("agent_session") or {}).get("value")
        if p.get("agent") != "claude" or not sid or p.get("agent_status") not in ("idle", "done"):
            continue
        if sid not in pid_of or BYPASS_FLAG not in proc_args(pid_of[sid]):
            continue
        pane = p["pane_id"]
        before = perm_mode(_pane_lines(pane))
        if before in (None, "bypass", "default"):  # only a mode the footer names; "default" may be an undrawn footer
            continue
        label = (p.get("label") or "").strip("■ ") or sid[:8]
        if dry_run:
            log(f"permission mode: {label} ({pane}) is in {before} mode; would switch to bypass")
            out.append((pane, sid, before, None))
            continue
        after = ensure_bypass(lambda: _pane_lines(pane), lambda k: _herdr("pane", "send-keys", pane, k), log=log)
        log(f"permission mode: {label} ({pane}) {before} -> {after}")
        of_events.emit("permission_mode_restored", {"pane": pane, "session": sid, "before": before, "after": after},
                       session=sid)
        out.append((pane, sid, before, after))
    return out


def has_herdr_hook(d):
    try:
        with open(os.path.join(os.path.expanduser(d), "settings.json")) as fh:
            return "herdr-agent-state" in json.dumps((json.load(fh).get("hooks") or {}).get("SessionStart") or [])
    except Exception:
        return False


def ensure_herdr_integration(d, run=subprocess.run, dry_run=False):
    """herdr learns which session runs in a pane from its SessionStart hook, installed per config dir. Without it a
    moved session is unbound in herdr: no name, no relay delivery, `of inbox` 403 (2026-11-08: seven sessions moved
    to ~/.claude-d, whose settings had hooks but not herdr's). Returns True if it installed it."""
    if has_herdr_hook(d):
        return False
    if not dry_run:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
        if not is_default(d):
            env["CLAUDE_CONFIG_DIR"] = os.path.expanduser(d)
        out = run(["herdr", "integration", "install", "claude"], env=env, capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise MoveError(f"herdr integration install claude for {short(d)}: {(out.stderr or '').strip()[:200]}")
    return True


def ensure_trust(src, dst, cwd, dry_run=False):
    """('trusted', key) when the target already trusts the folder; ('carried', key) when the source's trust
    was copied to the target (the owner's earlier decision); raises MoveError(untrusted=cwd) otherwise."""
    cands = trust_candidates(cwd)
    k = trusted_key(dst, cwd, cands)
    if k:
        return "trusted", k
    k = trusted_key(src, cwd, cands) or trusted_key(os.path.expanduser("~/.claude"), cwd, cands)
    if not k:
        raise MoveError(f"folder {cwd} is trusted in no config; the owner decides (the tool never presses the "
                        f"trust dialog)", untrusted=cwd)
    if not dry_run:
        path = account_file(dst)
        with open(path) as fh:
            conf = json.load(fh)
        entry = (conf.setdefault("projects", {})).setdefault(k, {})
        entry["hasTrustDialogAccepted"] = True
        tmp = f"{path}.of-capacity.{os.getpid()}.tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(conf, fh, indent=2)
        os.replace(tmp, path)
        time.sleep(1)
        if trusted_key(dst, cwd, [k]) != k:
            raise MoveError(f"could not carry the trust of {k} into {short(path)} (the file was rewritten)")
    return "carried", k


def _herdr(*args, timeout=30):
    out = subprocess.run([os.environ.get("OF_HERDR", "herdr"), *args], capture_output=True, text=True, timeout=timeout)
    if out.returncode != 0:
        raise MoveError(f"herdr {' '.join(args[:3])}: {(out.stderr or out.stdout).strip()[:300]}")
    try:
        return json.loads(out.stdout).get("result")
    except ValueError:
        return out.stdout


CLEAR_INPUT = "\x03"  # Ctrl-C as a raw byte: drops the whole input, continuation lines included; send-keys "ctrl+c"
# would arrive as a kitty sequence zsh types out (its "ctrl+u" typed "7;5u")


def _run_in_pane(pane, cmd, herdr=None):
    """Run `cmd` at the pane's shell prompt, its input emptied first (Ctrl-C).

    2026-11-12: the shell left behind by a session the founder closed by accident held a recalled history line (an
    older resume of another position's session). `herdr pane run` appended the resume to it, so that other session
    started a second copy in this pane, on another account, and the intended resume was chained behind it with
    `&&`. Nothing typed before the command may survive: Ctrl-C, not Ctrl-U, which empties only the current line and
    leaves a multi-line entry or a continuation prompt (of_review on PR 101)."""
    herdr = herdr or _herdr
    herdr("pane", "send-text", pane, CLEAR_INPUT)
    time.sleep(0.3)
    return herdr("pane", "run", pane, cmd)


def _agent_by_session(sid):
    for a in _herdr("agent", "list")["agents"]:
        if ((a.get("agent_session") or {}).get("value")) == sid:
            return a
    return None


def rename_agent(pane, name, sid, timeout=60, step=2):
    """herdr reports a resumed process as a new agent in its pane, without the name `of apply` gave the old
    one (platform_owner lost its name on 2026-11-06). Wait until herdr shows the session in the pane, then
    name it. Returns "named", "kept" (already named) or "unseen" (herdr never showed it; `of apply` may)."""
    def seen():
        return next((a for a in _herdr("agent", "list")["agents"] if a.get("pane_id") == pane
                     and (a.get("agent_session") or {}).get("value") == sid), None)
    a = _wait(seen, timeout, step=step)
    if not a:
        return "unseen"
    if a.get("name") == name:
        return "kept"
    _herdr("agent", "rename", pane, name)
    return "named"


def _pane_lines(pane, n=40):
    r = _herdr("pane", "read", pane, "--source", "visible", "--lines", str(n), "--format", "ansi")
    text = r.get("read", {}).get("text") if isinstance(r, dict) else r
    if isinstance(r, dict) and text is None:
        text = json.dumps(r)
        for k in ("text", "content", "output"):
            if isinstance(r.get(k), str):
                text = r[k]
    return (text or "").splitlines()


def _continuation(sid):
    try:
        import of_store
        row = of_store.connect().execute("SELECT payload FROM event WHERE name='of.continuation' AND session_id=? "
                                         "ORDER BY ts DESC LIMIT 1", (sid,)).fetchone()
        return json.loads(row[0]) if row and row[0] else {}
    except Exception:
        return {}


def _wait(cond, timeout, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    return cond()


def _inject(sid, text, reason):
    import hashlib
    digest = hashlib.sha256(text.strip().encode()).hexdigest()
    of_events.emit("injected_prompt", {"injector": "of_capacity", "target_session": sid, "text_sha256": digest,
                                       "reason": reason}, position=actor())
    path = os.path.join(STATE_DIR, "injections.jsonl")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as fh:
        fh.write(json.dumps({"ts": time.time(), "session": sid, "sha256": digest, "injector": "of_capacity"}) + "\n")


EXIT_DIALOG = "Background work is running"
EXIT_STOP = "Exit and stop tasks"
WORKTREE_DIALOG = "Exiting worktree session"  # a session inside a git worktree (EnterWorktree) asks on /exit
WORKTREE_KEEP = "Keep worktree"                # "Keep worktree" or "Keep worktree and tmux session": nothing is lost
DIALOG_FOOTER = "Esc to cancel"
OPTION = _re.compile(r"^\s*❯\s*(\d+)\.\s*(.*)$")


def _dialog_cursor(lines, title):
    plain = [ANSI.sub("", x) for x in lines]
    at = next((i for i, x in enumerate(plain) if title in x), None)
    if at is None:
        return None
    m = next((OPTION.match(x) for x in plain[at:] if OPTION.match(x)), None)
    return m.group(2).strip() if m else ""


def _exit_dialog(lines):
    """The cursor's option in Claude Code's exit dialog ("Background work is running"), or None if it is not open."""
    return _dialog_cursor(lines, EXIT_DIALOG)


def _worktree_dialog(lines):
    """The cursor's option in Claude Code's "Exiting worktree session" dialog (keep or remove the worktree), or None."""
    return _dialog_cursor(lines, WORKTREE_DIALOG)


def _a_dialog(lines):
    """A dialog that would swallow typed text: the two above, or any other whose last lines say "Esc to cancel"."""
    if _exit_dialog(lines) is not None or _worktree_dialog(lines) is not None:
        return True
    tail = [x for x in (ANSI.sub("", y).strip() for y in lines) if x][-4:]
    return any(DIALOG_FOOTER in x for x in tail)


def exit_session(pid, type_exit, read, key, allow_shells=False, wait=50, settle=8, step=1, log=print):
    """Type /exit and see it through by the screen; return once the process has exited.

    aws_keeper, 2026-11-07: a blind Enter 20 s after /exit missed the "Background work is running" dialog. The
    move failed and left the dialog open for 2.5 hours; two messages typed into it were lost, and someone finally
    picked "Move to background and exit" there. Now: answer the dialog only once it is on screen, and only with
    "Exit and stop tasks" (the cursor is checked; "Move to background" is never picked). A /exit still in the
    input box is submitted once. Without `allow_shells`, the dialog is cancelled. On any failure the dialog is
    closed with Esc ("Esc to cancel"), so the session stays as it was; `stuck` says when that did not work.

    2026-11-12, a user organization's session: inside a git worktree, a session answers /exit with "Exiting worktree session"
    (keep or remove it), which this did not read. The move gave up after 50 s and reported the dialog closed while
    it stayed open; two relay messages went into it and were lost, and the second one's Enter exited the session
    37 minutes later. Now that dialog is answered with "Keep worktree" (its default: the worktree, its branch and
    files stay, and the resume starts in it), and a failure counts any dialog still on screen as open."""
    def press(k):  # a key the pane rejects ends the attempt, never the restore below
        try:
            key(k)
            return True
        except Exception as e:  # noqa: BLE001
            log(f"  key {k}: {e}")
            return False
    type_exit()
    end, enters, ups, submitted, why = time.time() + wait, 0, 0, False, "did not exit"
    kept = 0
    while time.time() < end and _alive(pid):
        lines = read()
        wt = _worktree_dialog(lines)
        if wt is not None:  # keep the worktree: nothing is lost, with or without allow_shells
            if wt.startswith(WORKTREE_KEEP):
                if kept >= 2:
                    why = "the worktree exit dialog ignored Enter twice"
                    break
                if not press("Enter"):
                    why = "the pane rejected Enter"
                    break
                kept += 1
                _wait(lambda: not _alive(pid), settle, step=step)
                continue
            if ups >= 4:
                why = f"could not bring the cursor to {WORKTREE_KEEP!r} (it is on {wt!r})"
                break
            if not press("Up"):
                why = "the pane rejected Up"
                break
            ups += 1
            time.sleep(0.3)
            continue
        cur = _exit_dialog(lines)
        if cur is not None:
            if not allow_shells:
                why = "exiting would stop its background work or scheduled tasks; rerun with --allow-shells"
                break
            if cur.startswith(EXIT_STOP):
                if enters >= 2:
                    why = "the exit dialog ignored Enter twice"
                    break
                if not press("Enter"):
                    why = "the pane rejected Enter"
                    break
                enters += 1
                _wait(lambda: not _alive(pid), settle, step=step)
                continue
            if ups >= 4:
                why = f"could not bring the cursor to {EXIT_STOP!r} (it is on {cur!r})"
                break
            if not press("Up"):
                why = "the pane rejected Up"
                break
            ups += 1
            time.sleep(0.3)
            continue
        if not submitted and input_draft(lines) == "/exit":
            press("Enter")  # the /exit is still in the input box: submit it, once
            submitted = True
        time.sleep(step)
    if not _alive(pid):
        return
    for pause in (0, 2, 5, 10):  # a closed dialog can come back (2026-11-07: accounting_keeper's, open for 21 hours)
        time.sleep(pause * step)
        if _a_dialog(read()):
            press("Esc")
            _wait(lambda: not _a_dialog(read()), settle, step=step)
    if not _alive(pid):  # Esc on the worktree dialog means "keep" and exits: the move goes on
        return
    left = _a_dialog(read())
    log(f"  exit failed ({why}); the dialog is {'STILL OPEN' if left else 'closed, the session stays'}")
    raise MoveError(f"pid {pid}: {why}; " + ("the exit dialog is still open in its pane: look at it now"
                                             if left else "closed the exit dialog, the session stays as it was"),
                    stuck=left or None)


START_WAIT_S = int(os.environ.get("OF_CAPACITY_START_WAIT_S") or 300)  # the lab sets a few seconds; stay under
# the hook's 10-minute window (of_hook INJECT_SESSION_S)


def start_session(sid, name, cwd, target, prompt, run, read, key, reason, log=print, wait=START_WAIT_S, step=2):
    """Declare the first prompt (`_inject`), start the session with it (`run`) and wait until it is live.

    The hook takes a declaration for up to 10 minutes before the prompt (of_hook INJECT_SESSION_S). A start held
    longer lets the note in later as the owner's words: the folder trust dialog held platform_owner's resume
    from 09:27Z to 12:30Z on 2026-11-06. So when the session is not up in time and the trust dialog holds it,
    close the dialog with "No, exit" (grants nothing; the process exits and the note is never typed) and fail.
    With no dialog on screen the start may only be slow: declare once more, which covers ten more minutes."""
    _inject(sid, prompt, reason)
    t0 = time.time()
    time.sleep(1)
    run()
    new = _wait(lambda: next((x for x in live_sessions(target) if x["session"] == sid), None), wait, step=step)
    if not new:
        new = next((x for x in live_sessions(target) if x.get("name") == name and x.get("cwd") == cwd
                    and os.path.getmtime(x["file"]) >= t0), None)
    if new:
        return new
    screen = [ANSI.sub("", x) for x in read()]
    if any(BYPASS_DIALOG.search(x) for x in screen):
        key("Esc")
        raise MoveError(f"{name}: the start stopped at the Bypass Permissions warning on {short(target)}; closed it with "
                        f"\"No, exit\" (grants nothing), so the note was never typed. The account lacks the owner's "
                        f"skipDangerousModePermissionPrompt (a move carries it from the source now); then "
                        f"`of capacity resume {name} --session {sid} --to {short(target)} --pane <pane>`")
    if any(TRUST_DIALOG.search(x) for x in screen):
        key("Esc")
        raise MoveError(f"{name}: the start stopped at the folder trust dialog for {cwd}; closed it with \"No, exit\" "
                        f"(grants nothing), so the note was never typed. The owner decides the trust (of ask); then "
                        f"`of capacity resume {name} --session {sid} --to {short(target)} --pane <pane>`", untrusted=cwd)
    _inject(sid, prompt, reason + " (start still pending)")
    raise MoveError(f"{name}: no session came up on {short(target)} after {wait}s; read the pane. Its note is declared "
                    f"for ten more minutes; if it starts later than that, the hook records the note as the owner's")


def _zellij(session, *args, timeout=20):
    out = subprocess.run(["zellij", "--session", session, "action", *args], capture_output=True, text=True,
                         timeout=timeout)
    if out.returncode != 0:
        raise MoveError(f"zellij {' '.join(args[:2])}: {(out.stderr or out.stdout).strip()[:300]}")
    return out.stdout


def hold_after_move(session, now=None):
    """Any move holds the session on its new account for the cooldown, as the balancer's own moves do, so fill and
    relief do not undo a deliberate one (2026-11-11: of_docs was moved by hand to the account that can read a shared
    artifact, and fill prefers the account whose capacity expires first). Rescue still moves it if it stops."""
    try:
        c = load_cache()
        c.setdefault("_balanced", {})[session] = [now or time.time(), float(load_policy().get("cooldown_h", 2)) * 3600]
        save_cache(c)
    except Exception:
        pass


def move(p, target, dry_run=False, allow_shells=False, log=print, of_bin=None, registry_file=None, place=None,
         extra_note=None, in_place=False, assume_empty_input=False, model=None, why=None, allow_unsigned=False,
         ask=None, restart=False):
    """Move one position's live session to the config dir `target`. Raises MoveError before anything
    irreversible when a precondition fails; the old session then keeps running untouched.
    The session may run in a herdr pane (it resumes in the same pane) or in a Zellij pane (it resumes
    in a new herdr pane in `place` = (workspace label, tab label); the Zellij pane is left at its shell).
    `model` restarts it on another model (on the same account when `target` is None); `why` goes into its note.
    `restart` allows a restart on its own account and model, so the session starts with what a start gives it now
    (2026-11-12: two keepers moved with a partial tool profile got their tools back this way).
    An account where one of the position's account-bound tools is not signed in stops the move and files the
    founder's task for that account (docs/tools.md §5); `allow_unsigned` (a rescue) moves anyway, says so in the
    note and files the task. `ask`: the balancer's, else `of ask`."""
    name, sid = p["name"], ((p.get("holder") or {}).get("session") or "").strip()
    if not sid:
        raise MoveError(f"{name}: no holder.session in the registry")
    if not target and not model and not restart:
        raise MoveError(f"{name}: give a target config dir, a model, or both")
    sessions = {d: live_sessions(d) for d in config_dirs()}
    src = dir_of_position(p, sessions)
    if src is None:
        raise MoveError(f"{name}: session {sid[:8]} is not live in any config dir")
    target = os.path.abspath(os.path.expanduser(target)) if target else src
    if not os.path.isdir(target) or account_of(target) is None:
        raise MoveError(f"{short(target)}: not a config dir with an account")
    unsigned = missing_signins(name, target) if target != src else []
    if unsigned:
        names = ", ".join(i for i, _ in unsigned)
        if not allow_unsigned:
            if not dry_run:
                file_signin_task(target, unsigned, name, ask=ask, of_bin=of_bin, log=log)
            raise MoveError(f"{name}: not signed in on {short(target)}: {names} (docs/tools.md §5)"
                            + ("" if dry_run else "; the founder has the task"))
        log(f"  not signed in on {short(target)}: {names}; moving anyway (a rescue)")
        extra_note = (f"Your tool(s) {names} are not signed in on {account_of(target) or short(target)}, so they are "
                      f"missing in this session; the founder has the task to sign in. If work waits on them, tell "
                      f"of_capacity (of send of_capacity)." + ("\n\n" + extra_note if extra_note else ""))
        if not dry_run:
            file_signin_task(target, unsigned, name, ask=ask, of_bin=of_bin, moved=True, log=log)
    ctx_model = last_model(find_transcript(src, sid))
    switch = bool(model) and model != ctx_model  # the model changes: a restart on the same account is the point
    s = next(x for x in sessions[src] if x["session"] == sid)
    cont = _continuation(sid)
    if s["status"] == "busy":
        raise MoveError(f"{name}: busy (a turn or subagents running); move it between turns")
    if s["status"] not in ("idle", "shell", "waiting"):
        raise MoveError(f"{name}: session status {s['status']!r}")
    if s["status"] == "shell" and not allow_shells:
        tasks = [t.get("description") for t in cont.get("background_tasks") or [] if t.get("status") == "running"]
        raise MoveError(f"{name}: background shells are running ({'; '.join(map(str, tasks)) or 'see the pane'}); "
                        "exiting stops them. Agree with the holder, then pass --allow-shells")
    env, args = proc_env(s["pid"]), proc_args(s["pid"])
    a = _agent_by_session(sid)
    if a:
        runtime, pane = "herdr", a["pane_id"]
        if src == target and not switch and not restart:
            raise MoveError(f"{name}: already on {short(target)} in herdr pane {pane}"
                            + (f", on {ctx_model}" if model else ""))
        lines = _pane_lines(pane)
        if a.get("agent_status") not in ("idle", "done") and not limit_cursor(lines) and _exit_dialog(lines) is None:
            # a session stopped on its account's limit is "blocked" in herdr: that is the case a move is for
            raise MoveError(f"{name}: herdr says {a.get('agent_status')} in {pane}")
    elif env.get("ZELLIJ_PANE_ID") and env.get("ZELLIJ_SESSION_NAME"):
        runtime, zs, zp = "zellij", env["ZELLIJ_SESSION_NAME"], env["ZELLIJ_PANE_ID"]
        pane = f"zellij {zs}:{zp}"
        if in_place:
            place = None
        elif not place:
            raise MoveError(f"{name}: runs in {pane}; give the herdr workspace and tab to resume in (--into W/T)")
        lines = _zellij(zs, "dump-screen", "--pane-id", zp, "--ansi").splitlines()[-60:]
    else:
        raise MoveError(f"{name}: neither a herdr pane nor a Zellij pane holds session {sid[:8]} (pid {s['pid']})")
    transcript = find_transcript(src, sid)
    question = pending_question(transcript)
    if question and any("Esc to cancel" in ANSI.sub("", x) and "Enter to select" in ANSI.sub("", x) for x in lines):
        log(f"  open question to the founder ({question[1]}): carried into the hand-over note")
        if not dry_run:  # Esc closes the dialog; the turn may then hit the old account's limit dialog
            if runtime == "herdr":
                _herdr("pane", "send-keys", pane, "esc")
            else:
                _zellij(zs, "send-keys", "--pane-id", zp, "Esc")
            time.sleep(6)
            lines = _pane_lines(pane) if runtime == "herdr" else \
                _zellij(zs, "dump-screen", "--pane-id", zp, "--ansi").splitlines()[-60:]
    if question:
        extra_note = question_note(*question) + ("\n\n" + extra_note if extra_note else "")
    running = background_processes(s["pid"])
    if running:
        log(f"  background processes that the exit stops: {len(running)}")
        extra_note = ("These background processes were running under your old process and stopped with it "
                      "(from `ps` at the move). Re-create the ones you still need:\n" +
                      "\n".join(f"- `{c}`" for c in running) + ("\n\n" + extra_note if extra_note else ""))
    if runtime == "herdr" and _exit_dialog(lines) is not None and not dry_run:
        # an exit dialog left open (by anyone): Esc means "Stay", always safe, then the move goes on
        _herdr("pane", "send-keys", pane, "esc")
        time.sleep(2)
        lines = _pane_lines(pane)
    if any(LIMIT_DIALOG in ANSI.sub("", x) for x in lines) and not dry_run:
        # the old account's limit dialog: Enter only on "Stop and wait" (never usage credits, never "wait here")
        if not limit_cursor(lines):
            raise MoveError(f"{name}: the limit dialog's cursor is not on {LIMIT_DIALOG!r} in {pane}; left as it is")
        if runtime == "herdr":
            _herdr("pane", "send-keys", pane, "enter")
            time.sleep(2)
            lines = _pane_lines(pane)
        else:
            _zellij(zs, "send-keys", "--pane-id", zp, "Enter")
            time.sleep(2)
            lines = _zellij(zs, "dump-screen", "--pane-id", zp, "--ansi").splitlines()[-60:]
    cancelled_wait = False
    if any(AUTO_CONTINUE.search(ANSI.sub("", x)) for x in lines[-12:]) and not dry_run:
        # a session waiting out its limit ("continuing automatically at …, Esc to cancel"): Esc cancels only that wait,
        # and the move gives it capacity now (2026-11-07: 13 sessions held on an overflowed Pro account). The input
        # box can take a few seconds to come back; a single read missed it and left a watcher position with its wait
        # cancelled and no move (2026-11-11), so read until it shows.
        cancelled_wait = True
        for _ in range(AFTER_ESC_READS):
            if runtime == "herdr":
                if _ == 0:
                    _herdr("pane", "send-keys", pane, "esc")
                time.sleep(2)
                lines = _pane_lines(pane)
            else:
                if _ == 0:
                    _zellij(zs, "send-keys", "--pane-id", zp, "Esc")
                time.sleep(2)
                lines = _zellij(zs, "dump-screen", "--pane-id", zp, "--ansi").splitlines()[-60:]
            if input_draft(without_auto_continue(lines)) is not None:  # the cancelled notice stays in the history
                break
    draft = input_draft(without_auto_continue(lines) if dry_run or cancelled_wait else lines)
    if draft is None and assume_empty_input and s["status"] == "idle" and \
            not any(DIALOG_MARK.search(ANSI.sub("", x)) for x in lines[-25:]):
        log(f"  input box not drawn in {pane} (pane too small); no dialog on screen, session idle: proceeding")
        draft = ""
    if draft is None:
        raise MoveError(f"{name}: no input box visible in {pane} (a dialog?)", cancelled_wait=cancelled_wait)
    if draft:
        raise MoveError(f"{name}: the input box in {pane} holds a draft: {draft[:80]!r}", cancelled_wait=cancelled_wait)
    used, there = connectors_used(transcript), connectors_seen(target)
    missing = sorted(used - there) if there is not None else []
    plugs = sorted(x.split(":", 1)[1]
                   for x in lacking(tools_used(transcript), account_tools(None, plugins_ready(target))))
    if plugs:  # plugins are installed and signed in per config dir (2026-11-11: aws_keeper lost Cloudflare twice)
        log(f"  plugins not installed or not signed in on {short(target)}: {', '.join(plugs)}")
        extra_note = (f"Your plugin(s) {', '.join(plugs)} are not installed or not signed in on "
                      f"{account_of(target) or short(target)}, "
                      f"so their tools (mcp__plugin_…) are gone in this session. If work waits on them, tell "
                      f"of_capacity (of send of_capacity) what waits: it moves you to an account that has them or has "
                      f"them installed here. Do not work around them." + ("\n\n" + extra_note if extra_note else ""))
    if missing:  # account-level connectors do not travel with the session (2026-11-07: Slack)
        log(f"  claude.ai connectors not seen on {short(target)}: {', '.join(missing)}")
        extra_note = (f"Your claude.ai connector(s) {', '.join(missing)} are tied to the old account and have not been "
                      f"seen on {account_of(target) or short(target)}. If a call to them fails, do not retry around "
                      f"it: give the founder a task (of ask --task) to connect {', '.join(missing)} at claude.ai, "
                      f"Settings, Connectors, signed in as {account_of(target) or 'this account'}, and say what "
                      f"waits on it." + ("\n\n" + extra_note if extra_note else ""))
    trust = ensure_trust(src, target, s["cwd"], dry_run=True)  # refuses before anything irreversible
    # the effort the process runs at (its --effort), else the account's setting for the model: 40 of 49 sessions
    # ran at xhigh on accounts set to high (2026-11-10), and a move by the setting would have lowered them
    effort = flags_from_args(args)[1] or (effort_of(src, ctx_model) if ctx_model else None)
    model = model or ctx_model
    restarted = bool(restart) and src == target and not switch
    what = "restart" if restarted else "model change" if switch and src == target else "account move"
    bypass = "--dangerously-skip-permissions" in args or "bypassPermissions" in args
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    work = os.path.join(STATE_DIR, "capacity", "moves", f"{name}-{stamp}")
    note_path = os.path.join(work, "note.md")
    import of_memory
    on_own = bool(flags_from_args(args)[3])  # started by of spawn on its own directory: keep it
    mem_dir = of_memory.directory(p) if on_own or load_policy().get("position_memory") else None
    prompt = launch_prompt(note_path, model=model if switch else None, restarted=restarted)
    cmd = launch_command(s["cwd"], target, sid, name, note_path, model=model, effort=effort, bypass=bypass,
                         memory_dir=mem_dir, prompt=prompt, deny=deny_for(p, log), tools=tools_for(p, sid, target, log))
    key = project_key(src, sid)
    mem_src, mem_dst = os.path.join(src, "projects", key, "memory"), os.path.join(target, "projects", key, "memory")
    mem_base = os.path.join(STATE_DIR, "capacity", "memory-base", short(target).strip("~/"), key)
    where = f"{pane}" + (f" -> herdr {place[0]}/{place[1]}" if runtime == "zellij" and place else "")
    log(f"{name}: {short(src)} -> {short(target)} ({account_of(target)}), session {sid}, {where}, pid {s['pid']}")
    log(f"  was: {args[:160]}")
    log(f"  context model {ctx_model}" + (f" -> {model}" if switch else "")
        + f", effort {effort}; transcript {os.path.getsize(transcript) // 1024} KB")
    log(f"  files: {', '.join(session_paths(src, sid))}")
    if mem_dir:  # the folder memory is not merged: the position's directory is read from every account
        seeded = seed_memory([p], only=(p["_org"], name), extra_sessions={sid: (p["_org"], name)}, dry_run=True,
                             log=log)["positions"].get(mem_dir) or []
        log(f"  memory: the position's own {short(mem_dir)}; from the folder: {seeded or 'nothing new'}")
    elif src != target:
        log(f"  memory {key}: {[x for x in merge_memory(mem_src, mem_dst, mem_base, '/nonexistent', dry_run=True) if x[1] != 'same']}")
    log(f"  resume: {cmd}")
    log(f"  folder trust: {trust[0]} ({trust[1]})")
    if dry_run:
        return {"dry_run": True, "command": cmd}
    if trust[0] == "carried":
        trust = ensure_trust(src, target, s["cwd"])
        log(f"  folder trust carried: {trust[1]} from {short(src)}")
    carried_settings = ensure_settings(src, target) if src != target else []
    if carried_settings:
        log(f"  settings carried from {short(src)}: {', '.join(carried_settings)}")
    if runtime == "herdr" or place:
        if ensure_herdr_integration(target):
            carried_settings.append("herdr integration")
            log(f"  herdr's Claude integration installed in {short(target)}")
    os.makedirs(work, mode=0o700, exist_ok=True)
    with open(note_path, "w") as fh:
        fh.write(handover_note(name, sid, src, target, account_of(target), cont, _iso(time.time()),
                               (extra_note or "").replace("<name>", name) or None,
                               model=(ctx_model, model) if switch else None, why=why, restarted=restarted))
    held = False  # a Zellij pane whose command is claude itself is "held" after exit; Esc drops it to a shell
    if runtime == "zellij":
        try:
            ppid = subprocess.run(["ps", "-o", "ppid=", "-p", str(s["pid"])], capture_output=True, text=True).stdout.strip()
            held = "zellij" in proc_args(ppid)
        except Exception:
            held = False
    # 1. exit between turns, by the screen ("Background work is running": scheduled tasks, background shells)
    _inject(sid, "/exit", f"{what}: exit")
    if runtime == "herdr":
        type_exit = lambda: _herdr("agent", "prompt", pane, "/exit")  # noqa: E731
        read_old = lambda: _pane_lines(pane, 60)  # noqa: E731
        key_old = lambda k: _herdr("pane", "send-keys", pane, k.lower())  # noqa: E731
    else:
        def type_exit():
            _zellij(zs, "write-chars", "--pane-id", zp, "/exit")
            time.sleep(0.3)
            _zellij(zs, "send-keys", "--pane-id", zp, "Enter")
        read_old = lambda: _zellij(zs, "dump-screen", "--pane-id", zp, "--ansi").splitlines()[-60:]  # noqa: E731
        key_old = lambda k: _zellij(zs, "send-keys", "--pane-id", zp, k)  # noqa: E731
    try:
        exit_session(int(s["pid"]), type_exit, read_old, key_old, allow_shells=allow_shells, log=log)
    except MoveError as e:
        raise MoveError(f"{name}: {e} ({pane}); the hand-over note was not used", stuck=e.stuck)
    # 2. the pane to resume in
    if runtime == "herdr" or (runtime == "zellij" and not place):
        new_pane = pane
    else:
        c, follow = place_command(place[0], place[1], s["cwd"], _herdr("workspace", "list")["workspaces"],
                                  _herdr("tab", "list")["tabs"], _herdr("pane", "list")["panes"])
        new_pane = first_pane_id(_herdr(*c))
        if not new_pane:
            raise MoveError(f"{name}: herdr {' '.join(c[:2])} returned no pane; the session files are untouched")
        if follow:
            tab_id = _herdr("pane", "get", new_pane)
            tab_id = (tab_id.get("pane") or tab_id).get("tab_id") if isinstance(tab_id, dict) else None
            if tab_id:
                _herdr("tab", "rename", tab_id, follow[1])
        log(f"  new herdr pane {new_pane} in {place[0]}/{place[1]}")
    if runtime == "zellij" and not place:
        if held:
            time.sleep(1)
            _zellij(zs, "send-keys", "--pane-id", zp, "Esc")
        shell_ready = lambda: (time.sleep(2) or True)  # noqa: E731  the pid is gone; the shell redraws its prompt
    else:
        shell_ready = lambda: not any(  # noqa: E731
            "claude" in " ".join(fp.get("argv") or [])
            for fp in (_herdr("pane", "process-info", "--pane", new_pane)["process_info"].get("foreground_processes") or []))
    if not _wait(shell_ready, 15):
        raise MoveError(f"{name}: pane {new_pane} is not at its shell prompt; the session files are untouched")
    # 3. carry files and memory (after exit, so the transcript is final)
    files, mem, conflicts = [], [], []
    if src != target:
        files = copy_session(src, target, sid, os.path.join(work, "backup"))
        mark_copied([os.path.join(target, rel) for kind, rel in files if kind == "copy"])
        if not mem_dir:
            mem = merge_memory(mem_src, mem_dst, mem_base, os.path.join(work, "conflicts"))
        log(f"  carried {len([f for f in files if f[0] == 'copy'])} path(s); memory: "
            f"{', '.join(f'{f}:{a}' for f, a in mem if a != 'same') or 'unchanged'}")
    if mem_dir:  # what the session wrote to its folder before it ran on its own directory
        mem = seed_memory([p], only=(p["_org"], name), extra_sessions={sid: (p["_org"], name)},
                          log=log)["positions"].get(mem_dir) or []
        log(f"  memory: {short(mem_dir)}, from the folder: {', '.join(f'{f}:{a}' for f, a in mem) or 'nothing new'}")
        conflicts = [f for f, act in mem if act == "conflict"]
        if conflicts:
            log(f"  MEMORY CONFLICTS to merge by hand: {', '.join(conflicts)} (source copies in {work}/conflicts)")
    # 4. resume, the note declared right before (start_session)
    if runtime == "zellij" and not place:
        def run():
            _zellij(zs, "write-chars", "--pane-id", zp, CLEAR_INPUT)  # as _run_in_pane: nothing typed before survives
            time.sleep(0.3)
            _zellij(zs, "write-chars", "--pane-id", zp, cmd)
            time.sleep(0.3)
            _zellij(zs, "send-keys", "--pane-id", zp, "Enter")
        read = lambda: _zellij(zs, "dump-screen", "--pane-id", zp, "--ansi").splitlines()[-60:]  # noqa: E731
        key = lambda k: _zellij(zs, "send-keys", "--pane-id", zp, k)  # noqa: E731
    else:
        run = lambda: _run_in_pane(new_pane, cmd)  # noqa: E731
        read = lambda: _pane_lines(new_pane)  # noqa: E731
        key = lambda k: _herdr("pane", "send-keys", new_pane, k.lower())  # noqa: E731
    new = start_session(sid, name, s["cwd"], target, prompt, run, read, key, f"{what}: hand-over note", log=log)
    log(f"  resumed: pid {new['pid']}, session {new['session']}, pane {new_pane}")
    if bypass and (runtime != "zellij" or place):  # a herdr pane: the footer can be read and keyed
        check_bypass_after_start(new_pane, log)
    # 5. record and rebind
    diff_path = None
    if registry_file:
        wp = None
        if runtime == "zellij" and place:
            wp = f"herdr local, workspace {place[0]}, tab {place[1]}, pane {new_pane}"
        _, diff_path = propose_registry(registry_file, name, short(target), wp, work)
        if diff_path:
            log(f"  registry: proposed diff {diff_path} (for the organization's structure keeper; not applied)")
    ws = {"config_dir": target, "account": account_of(target), "runtime": "zellij" if new_pane == pane and runtime == "zellij"
          else "herdr", "pane": new_pane}
    prev = {"config_dir": src, "account": account_of(src), "runtime": runtime, "pane": pane}
    of_events.emit("holder_changed", {"position": name, "previous_session": sid, "new_session": new["session"],
                                      "evidence": "of capacity move" + (" --model" if switch else "")
                                                  + (" --restart" if restarted else ""),
                                      "workplace": ws, "previous_workplace": prev,
                                      "model": model, "effort": effort, "files": [r for _, r in files],
                                      **({"previous_model": ctx_model, "reason": why} if switch else {}),
                                      **({"reason": why} if restarted and why else {}),
                                      "memory": mem, "registry_diff": diff_path,
                                      "trust": {"state": trust[0], "path": trust[1],
                                                **({"from": src} if trust[0] == "carried" else {})},
                                      "settings_carried": carried_settings},
                   position=name, session=new["session"])
    hold_after_move(new["session"])
    if ws["runtime"] == "herdr":
        try:
            log(f"  herdr name: {rename_agent(new_pane, name, new['session'])}")
        except MoveError as e:
            log(f"  herdr name: not set ({e}); `of apply` sets it")
    if of_bin:
        subprocess.run([sys.executable, of_bin, "apply"], capture_output=True, text=True, timeout=120)
    return {"position": name, "pane": new_pane, "session": new["session"], "pid": new["pid"], "work": work,
            "conflicts": conflicts, "registry_diff": diff_path}


def resume_note(name, sid, target, account, ts, last_ts, extra=None):
    out = [f"# Account move, finished late: {name}", "",
           f"`of_capacity` resumed this session (`{sid}`) on `{short(target)}` ({account}) at {ts}. An earlier move "
           f"did not finish its resume, so the session had no live holder after its last record ({last_ts or 'unknown'}). "
           "Same session, same pane, same name; your context, memory and files are where the move put them.", "",
           "Check that the move kept your context: reply in at most three lines with",
           "1. what you were working on before this message, and", "2. your next step.",
           "Then declare your state with `of status` as usual and carry on (or stay idle if you were waiting). "
           "Anything that arrived for you while you were down (relay messages, answers to your asks) is in "
           "`of inbox` and `of asks`.", "", HOLDER_LAG_NOTE]
    if extra:
        out += ["", "## From your organization", "", extra.strip()]
    return "\n".join(out) + "\n"


def resume(name, sid, target, pane, extra_note=None, log=print, of_bin=None, dry_run=False, position=None):
    """Finish a move whose resume never started (the folder trust dialog, a crash): start session `sid` from
    config dir `target` in herdr pane `pane` with a declared note, then name the agent and record the holder.

    Nothing is typed into a pane undeclared (of_platform, 2026-11-07): runners_keeper's relaunch note at 12:41Z
    on 2026-11-06 was typed by hand and recorded as the owner's words. A claude process sitting on the folder
    trust dialog in that pane is closed with "No, exit" first (grants nothing); any other claude process there
    is refused. The folder must already be trusted in `target`: the owner decides, never the tool."""
    target = os.path.expanduser(target)
    transcript = find_transcript(target, sid)
    if not transcript:
        raise MoveError(f"{name}: no transcript of session {sid} in {short(target)}")
    for d in config_dirs():
        if any(x["session"] == sid for x in live_sessions(d)):
            raise MoveError(f"{name}: session {sid[:8]} is live in {short(d)}; use `of capacity move`")
    cwd = last_field(transcript, "cwd")
    if not cwd or not os.path.isdir(cwd):
        raise MoveError(f"{name}: no working directory for session {sid[:8]} in its transcript")
    if not trusted_key(target, cwd):
        raise MoveError(f"{name}: folder {cwd} is not trusted in {short(target)}; the owner decides (of ask)",
                        untrusted=cwd)

    def claude_fg():
        info = _herdr("pane", "process-info", "--pane", pane)["process_info"]
        return [fp for fp in info.get("foreground_processes") or [] if "claude" in " ".join(fp.get("argv") or [])]
    stale = False
    if claude_fg():
        if not any(TRUST_DIALOG.search(ANSI.sub("", x)) for x in _pane_lines(pane)):
            raise MoveError(f"{name}: a claude process runs in {pane}; resume needs the pane at its shell")
        stale = True
    model = last_model(transcript)
    effort = effort_of(target, model) if model else None
    bypass = ran_in_bypass(transcript)  # not its last mode: a session that slipped to auto goes back to bypass
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    work = os.path.join(STATE_DIR, "capacity", "moves", f"{name}-{stamp}-resume")
    note_path = os.path.join(work, "note.md")
    org = (position or {}).get("_org")
    import of_memory
    mem_dir = of_memory.directory(position) if position and load_policy().get("position_memory") else None
    cmd = launch_command(cwd, target, sid, name, note_path, model=model, effort=effort, bypass=bypass, resumed=True,
                         memory_dir=mem_dir, deny=deny_for(position, log), tools=tools_for(position, sid, target, log))
    log(f"{name}: resume session {sid} on {short(target)} ({account_of(target)}) in herdr pane {pane}")
    log(f"  context model {model}, effort {effort}, permission mode {'bypass' if bypass else 'default'}")
    if stale:
        log("  a stale folder trust dialog holds the pane: closed with \"No, exit\" first (grants nothing)")
    log(f"  resume: {cmd}")
    if dry_run:
        return {"dry_run": True, "command": cmd}
    if stale:
        _herdr("pane", "send-keys", pane, "esc")
        if not _wait(lambda: not claude_fg(), 15):
            raise MoveError(f"{name}: the claude process in {pane} did not exit after \"No, exit\"; read the pane")
    if ensure_herdr_integration(target):
        log(f"  herdr's Claude integration installed in {short(target)}")
    if mem_dir:
        mem = seed_memory([position], only=(org, name), extra_sessions={sid: (org, name)}, log=log)["positions"]
        log(f"  memory: {short(mem_dir)}, from the folder: "
            f"{', '.join(f'{f}:{a}' for f, a in mem.get(mem_dir) or []) or 'nothing new'}")
    os.makedirs(work, mode=0o700, exist_ok=True)
    with open(note_path, "w") as fh:
        fh.write(resume_note(name, sid, target, account_of(target), _iso(time.time()),
                             last_field(transcript, "timestamp"), extra_note))
    new = start_session(sid, name, cwd, target, launch_prompt(note_path, resumed=True),
                        lambda: _run_in_pane(pane, cmd), lambda: _pane_lines(pane),
                        lambda k: _herdr("pane", "send-keys", pane, k.lower()), "account move: resume note", log=log)
    log(f"  resumed: pid {new['pid']}, session {new['session']}, pane {pane}")
    if bypass:
        check_bypass_after_start(pane, log)
    ws = {"config_dir": target, "account": account_of(target), "runtime": "herdr", "pane": pane}
    of_events.emit("holder_changed", {"position": name, "previous_session": sid, "new_session": new["session"],
                                      "evidence": "of capacity resume", "workplace": ws, "model": model,
                                      "effort": effort, "trust": {"state": "trusted", "path": trusted_key(target, cwd)}},
                   position=name, session=new["session"])
    try:
        log(f"  herdr name: {rename_agent(pane, name, new['session'])}")
    except MoveError as e:
        log(f"  herdr name: not set ({e}); `of apply` sets it")
    if of_bin:
        subprocess.run([sys.executable, of_bin, "apply"], capture_output=True, text=True, timeout=120)
    return {"position": name, "pane": pane, "session": new["session"], "pid": new["pid"], "work": work}


# ------------------------------------------------------------ reboot: the Mac restarts (founder, 2026-11-10)
#
# A restart of the Mac (the founder's OS update) stops every agent session and clears /private/tmp, where the
# sessions keep their scratchpads and their background tasks' output (30 GB, 227,000 files on 2026-11-10). The
# platform's services come back by launchd at login; the sessions do not. `prepare`, run just before the restart,
# records each live session's account, pane, model and flags and what it had running, and keeps /private/tmp by hard
# links (the same volume: no copy, no space, and a file still written to in place stays current). `restore`, run
# after it, puts the files back and resumes every session on its account with a declared note, in its own pane when
# herdr brought the pane back and in a new pane of the same workspace and tab otherwise, production first.

REBOOT_DIR = os.path.join(STATE_DIR, "capacity", "reboot")
_ARG_MODEL = _re.compile(r"(?:^|\s)--model[ =](\S+)")
_ARG_EFFORT = _re.compile(r"(?:^|\s)--effort[ =](\S+)")
_ARG_MEMORY = _re.compile(r'"autoMemoryDirectory"\s*:\s*"([^"]+)"')
_ARG_SETTINGS = _re.compile(r"(?:^|\s)--settings[ =]([^\s{'\"]\S*)")


def settings_text(args):
    """The JSON of a `--settings <file>` on a running harness's command line (of spawn and moves write one file,
    of_spawn.settings_file); "" when it names none or the file is gone."""
    m = _ARG_SETTINGS.search(args or "")
    try:
        with open(os.path.expanduser(m.group(1))) as fh:
            return fh.read(1 << 20)
    except (AttributeError, OSError, UnicodeDecodeError):
        return ""


def agent_tmp_root():
    return f"/private/tmp/claude-{os.getuid()}"


def flags_from_args(args):
    """(model, effort, bypass, memory dir) from a running harness's command line; None where it names none."""
    args = args or ""
    m, e = _ARG_MODEL.search(args), _ARG_EFFORT.search(args)
    mem = _ARG_MEMORY.search(args) or _ARG_MEMORY.search(settings_text(args))
    return m.group(1) if m else None, e.group(1) if e else None, BYPASS_FLAG in args, mem.group(1) if mem else None


def reboot_plan(positions, sessions, panes, workspaces, tabs, info, hold=()):
    """Pure: one entry per live session, production (`hold`) first, then by organization and name.
    sessions: {config dir: live_sessions(dir)}; panes, workspaces, tabs: herdr's lists; info: {session: {"args",
    "continuation", "running", "model", "effort", "bypass"}} read from the process and its transcript."""
    by_sid = {((p.get("holder") or {}).get("session") or "").strip(): p for p in positions}
    ws_label = {w.get("workspace_id"): w.get("label") for w in workspaces}
    tab_label = {t.get("tab_id"): t.get("label") for t in tabs}
    pane_of = {(a.get("agent_session") or {}).get("value"): a for a in panes if a.get("agent_session")}
    out = []
    for d, ss in sessions.items():
        for s in ss:
            sid, x = s["session"], info.get(s["session"]) or {}
            p, a, cont = by_sid.get(sid), pane_of.get(sid) or {}, x.get("continuation") or {}
            model, effort, bypass, mem = flags_from_args(x.get("args"))
            out.append({"name": p["name"] if p else (s.get("name") or sid[:8]), "position": p["name"] if p else None,
                        "org": p.get("_org") if p else None, "session": sid, "config_dir": d,
                        "status": s.get("status"), "cwd": s.get("cwd"), "pane": a.get("pane_id"),
                        "workspace": ws_label.get(a.get("workspace_id")), "tab": tab_label.get(a.get("tab_id")),
                        "model": model or x.get("model"), "effort": effort or x.get("effort"),
                        "bypass": bypass or bool(x.get("bypass")), "memory_dir": mem,
                        "crons": cont.get("session_crons") or [],
                        "tasks": [t for t in cont.get("background_tasks") or [] if t.get("status") == "running"],
                        "running": x.get("running") or [], "hold": bool(p) and p["name"] in hold})
    return sorted(out, key=lambda e: (not e["hold"], e["position"] is None, e["org"] or "", e["name"]))


TASK_OUTPUT_MAX = 64 * 1024 * 1024


def keep_tree(src, dst):
    """Hard-link every regular file under `src` into `dst`, same layout; symlinks are recreated, sockets and the
    like skipped. Returns {"files", "bytes", "failed"}."""
    import stat
    n = size = failed = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        tdir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(tdir, mode=0o700, exist_ok=True)
        for f in files + [x for x in dirs if os.path.islink(os.path.join(root, x))]:
            sp, tp = os.path.join(root, f), os.path.join(tdir, f)
            try:
                st = os.lstat(sp)
                if stat.S_ISLNK(st.st_mode):
                    os.symlink(os.readlink(sp), tp)
                elif stat.S_ISREG(st.st_mode) and os.path.basename(root) == "tasks":
                    # a background task's output: Claude Code kills the task when its file gains a second link
                    # (2026-11-10, the first prepare killed its own shell), so these are copied
                    if st.st_size > TASK_OUTPUT_MAX:
                        continue
                    shutil.copy2(sp, tp)
                    size += st.st_size
                elif stat.S_ISREG(st.st_mode):
                    os.link(sp, tp)
                    size += st.st_size
                else:
                    continue
                n += 1
            except FileExistsError:
                n += 1
            except OSError:
                failed += 1
    return {"files": n, "bytes": size, "failed": failed}


def put_back_tree(src, dst):
    """Move every file kept under `src` back under `dst` where nothing is there now, then remove `src` (the rest
    were second names of files that survived). Returns {"restored", "there"}."""
    restored = there = 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        tdir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(tdir, mode=0o700, exist_ok=True)
        links = [x for x in dirs if os.path.islink(os.path.join(root, x))]
        dirs[:] = [x for x in dirs if x not in links]
        for f in files + links:
            sp, tp = os.path.join(root, f), os.path.join(tdir, f)
            if os.path.lexists(tp):
                there += 1
                continue
            os.rename(sp, tp)
            restored += 1
    shutil.rmtree(src, ignore_errors=True)
    return {"restored": restored, "there": there}


HERDR_CONFIG = os.path.expanduser("~/.config/herdr/config.toml")
_HERDR_RESUME = _re.compile(r"(?m)^[ \t]*resume_agents_on_restore[ \t]*=[ \t]*(true|false)[ \t]*$")
_HERDR_SESSION = _re.compile(r"(?m)^\[session\][ \t]*$")


def herdr_resume_setting(text, on):
    """Pure: herdr's config text with `[session] resume_agents_on_restore` set to `on`, and the value it had (None:
    unset, herdr's default, which is on). After the restart of 2026-11-10 herdr resumed 38 agents itself as a bare
    `claude --resume <id>`: no account, so ~/.claude at 100% of its week and its stale transcript copies, and no
    bypass, model or effort."""
    m = _HERDR_RESUME.search(text)
    line = f"resume_agents_on_restore = {'true' if on else 'false'}"
    if m:
        return text[:m.start()] + line + text[m.end():], m.group(1) == "true"
    h = _HERDR_SESSION.search(text)
    if h:
        return text[:h.end()] + "\n" + line + text[h.end():], None
    note = ("# of capacity reboot turns this off while a restart is prepared: herdr's own resume starts agents with no\n"
            "# account or flags (2026-11-10); `restore` starts them as they ran.\n")
    return text.rstrip("\n") + ("\n\n" if text.strip() else "") + note + "[session]\n" + line + "\n", None


def set_herdr_resume(on, path=None, log=print):
    """Set herdr's agent resume on restart; returns the earlier value (None: unset). herdr reads it at start."""
    path = path or HERDR_CONFIG
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        text = ""
    new, prev = herdr_resume_setting(text, on)
    if new != text:
        with open(path, "w") as fh:
            fh.write(new)
        try:
            _herdr("server", "reload-config")
        except Exception as err:
            log(f"herdr reload-config: {err}")
    return prev


def reboot_note(e, account, ts, prepared_at, tmp_kept):
    lines = [f"# The Mac restarted: {e['name']}", "",
             f"The founder restarted the Mac (an OS update), and every agent session stopped with it. `of_capacity` "
             f"resumed this session (`{e['session']}`) on `{short(e['config_dir'])}` ({account}) at {ts}, on the "
             "same account and under the same name; your context, memory and files are carried.", ""]
    if e.get("status") == "busy":
        lines += [f"A turn of yours was running when the restart was prepared ({prepared_at}); the restart cut it "
                  "off. Check what it left half done before you go on.", ""]
    crons, tasks, running = e.get("crons") or [], e.get("tasks") or [], e.get("running") or []
    if crons or tasks or running:
        lines.append("Crons, monitors and background tasks died with the old process. Your last record of them:")
        lines += [f"- cron/wake-up: {json.dumps(c, ensure_ascii=False)[:2000]}" for c in crons]
        lines += [f"- background {t.get('type')}: {t.get('description') or ''} ({(t.get('command') or '')[:160]!r})"
                  for t in tasks]
        lines += [f"- process (from `ps` before the restart): `{c}`" for c in running]
        lines += ["Re-create the ones you still need. A time that has passed meanwhile needs a fresh look, not a "
                  "blind re-arm.", ""]
    else:
        lines += ["Your last record shows no crons, monitors or background tasks.", ""]
    if tmp_kept:
        lines += [f"The files under {agent_tmp_root()} (your scratchpad, your background tasks' output) were kept "
                  f"and put back; whatever was written there after {prepared_at} as a new file is lost.", ""]
    lines += ["Check that the restart kept your context: reply in at most three lines with",
              "1. what you were working on before the restart, and", "2. your next step.",
              "Then declare your state with `of status` as usual and carry on (or stay idle if you were waiting). "
              "Anything that arrived for you while you were down (relay messages, answers to your asks) is in "
              "`of inbox` and `of asks`.", "", HOLDER_LAG_NOTE]
    return "\n".join(lines) + "\n"


def carry_note(e, account, ts, plan):
    """The note for a session carried to another machine (of_carry; plan kind "carry")."""
    lines = [f"# Carried to another machine: {e['name']}", "",
             f"`of_capacity` carried this session (`{e['session']}`) from {plan.get('machine') or 'another machine'} "
             f"(home `{plan.get('home')}`) to this one (home `{plan.get('target_home') or os.path.expanduser('~')}`), "
             f"and resumed it on `{short(e['config_dir'])}` ({account}) at {ts}, under the same name, model and mode. "
             "Your transcript, memory and scratch files came with it.", "",
             f"Paths in your earlier messages name the old home, `{plan.get('home')}`: the same files are now under "
             f"`{plan.get('target_home') or os.path.expanduser('~')}`. Use the new paths from here on.", ""]
    crons, tasks, running = e.get("crons") or [], e.get("tasks") or [], e.get("running") or []
    if crons or tasks or running:
        lines.append("Crons, monitors and background tasks did not come with it. Your last record of them:")
        lines += [f"- cron/wake-up: {json.dumps(c, ensure_ascii=False)[:2000]}" for c in crons]
        lines += [f"- background {t.get('type')}: {t.get('description') or ''} ({(t.get('command') or '')[:160]!r})"
                  for t in tasks]
        lines += [f"- process (from `ps` before the carry): `{c}`" for c in running]
        lines += ["Re-create the ones you still need, on this machine. A time that has passed meanwhile needs a fresh "
                  "look, not a blind re-arm.", ""]
    lines += ["Check that the carry kept your context: reply in at most three lines with",
              "1. what you were working on before this message, and", "2. your next step.",
              "Then declare your state with `of status` as usual and carry on (or stay idle if you were waiting). "
              "Anything that arrived for you meanwhile is in `of inbox` and `of asks`.", "", HOLDER_LAG_NOTE]
    return "\n".join(lines) + "\n"


def _reboot_current():
    return os.path.join(REBOOT_DIR, "current")


def load_reboot_plan(path=None):
    try:
        with open(path or os.path.join(_reboot_current(), "plan.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def live_entries(positions, hold=(), log=print):
    """The reboot plan's entries for every live session on this machine: its account, pane, model and flags, and
    what it had running (reboot_plan). Read-only for the sessions; `reboot prepare` and `carry prepare` use it."""
    sessions = {d: live_sessions(d) for d in config_dirs()}
    try:
        panes, workspaces, tabs = (_herdr("pane", "list")["panes"], _herdr("workspace", "list")["workspaces"],
                                   _herdr("tab", "list")["tabs"])
    except MoveError as err:
        log(f"herdr: {err}; panes are not recorded, restore opens new ones")
        panes, workspaces, tabs = [], [], []
    info = {}
    for d, ss in sessions.items():
        for s in ss:
            tr = find_transcript(d, s["session"])
            model = last_model(tr) if tr else None
            info[s["session"]] = {"args": proc_args(s["pid"]), "continuation": _continuation(s["session"]),
                                  "running": background_processes(s["pid"]), "model": model,
                                  "effort": effort_of(d, model) if model else None,
                                  "bypass": ran_in_bypass(tr) if tr else False}
    return reboot_plan(positions, sessions, panes, workspaces, tabs, info, hold)


def reboot_prepare(positions, hold=(), dry_run=False, log=print, tmp_root=None, out_dir=None):
    """Record every live session and keep /private/tmp by hard links. Read-only for the sessions; run it again
    right before the restart (a later run replaces the earlier one)."""
    tmp_root = tmp_root or agent_tmp_root()
    out_dir = out_dir or _reboot_current()
    if os.path.realpath(out_dir).startswith(os.path.realpath(tmp_root) + os.sep):
        raise MoveError(f"{out_dir} is inside {tmp_root}, which the restart clears")
    entries = live_entries(positions, hold, log=log)
    plan = {"prepared_at": _iso(time.time()), "tmp_root": tmp_root, "entries": entries}
    for e in entries:
        extra = len(e["crons"]) + max(len(e["tasks"]), len(e["running"]))
        log(f"{e['name']:28} {short(e['config_dir']):26} {e['status'] or '?':6} pane {e['pane'] or '-':8} "
            f"{e['model'] or '?'}{' hold' if e['hold'] else ''}{f', {extra} to re-arm' if extra else ''}")
    if dry_run:
        log(f"{len(entries)} session(s); dry run: nothing written, {tmp_root} not kept")
        return plan
    earlier = load_reboot_plan(os.path.join(out_dir, "plan.json")) or {}
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, mode=0o700)
    prev = set_herdr_resume(False, log=log)
    plan["herdr_resume_before"] = earlier["herdr_resume_before"] if "herdr_resume_before" in earlier else prev
    log("herdr: resume_agents_on_restore = false until `restore` (herdr would start the agents with no account or "
        "flags)")
    if os.path.isdir(tmp_root):
        t0 = time.time()
        plan["tmp_kept"] = keep_tree(tmp_root, os.path.join(out_dir, "tmp"))
        k = plan["tmp_kept"]
        failed = f", {k['failed']} failed" if k["failed"] else ""
        log(f"{tmp_root}: {k['files']} file(s), {k['bytes'] / 1e9:.1f} GB kept by hard links in "
            f"{time.time() - t0:.0f}s{failed}")
    with open(os.path.join(out_dir, "plan.json"), "w") as fh:
        json.dump(plan, fh, indent=1, ensure_ascii=False)
    of_events.emit("capacity_reboot_prepared", {"sessions": len(entries), "tmp_kept": plan.get("tmp_kept")},
                   position=actor())
    log(f"{len(entries)} session(s) recorded in {short(out_dir)}; restart, log in, start herdr, then run "
        "`of capacity reboot restore` in a herdr pane")
    return plan


_ASK_MARK = _re.compile(r"\s*❓\d+$")  # the open-ask count herdr labels carry
_SHELLS = {"zsh", "bash", "sh", "fish", "login", "tcsh", "dash"}


def _shell_only(pane):
    """Nothing but a shell runs in the pane's foreground (a pane herdr brought back, or a fresh one)."""
    info = _herdr("pane", "process-info", "--pane", pane)["process_info"]
    return all(os.path.basename(((fp.get("argv") or ["?"])[0])).lstrip("-") in _SHELLS
               for fp in info.get("foreground_processes") or [])


def free_pane(panes, workspaces, tabs, ws_label, tab_label, cwd, claimed=(), own=None):
    """Pure: a pane herdr brought back with other ids: no agent, in the same workspace and tab (labels without the
    ask count), in the session's folder, not taken this run and not the pane `restore` runs in."""
    wsl = {w.get("workspace_id"): _ASK_MARK.sub("", w.get("label") or "") for w in workspaces}
    tbl = {t.get("tab_id"): _ASK_MARK.sub("", t.get("label") or "") for t in tabs}
    return next((x["pane_id"] for x in panes if x.get("pane_id") not in claimed and x.get("pane_id") != own
                 and not x.get("agent") and x.get("cwd") == cwd and wsl.get(x.get("workspace_id")) == ws_label
                 and tbl.get(x.get("tab_id")) == tab_label), None)


def _restore_one(e, p, plan, ts, log=print, dry_run=False, home=None, claimed=None):
    name, sid, target = e["name"], e["session"], e["config_dir"]
    transcript = find_transcript(target, sid)
    if not transcript:
        raise MoveError(f"no transcript of session {sid} in {short(target)}")
    # the folder the process started in (its transcript's folder), not the transcript's last cwd: platform_owner
    # started in ~/acme and works in ~/acme/org, which its account does not trust (2026-11-10)
    cwd = e.get("cwd") if e.get("cwd") and os.path.isdir(e["cwd"]) else last_field(transcript, "cwd")
    if not cwd or not os.path.isdir(cwd):
        raise MoveError(f"no working directory for session {sid[:8]}")
    if not trusted_key(target, cwd):
        raise MoveError(f"folder {cwd} is not trusted in {short(target)}; the owner decides (of ask)", untrusted=cwd)
    claimed = claimed if claimed is not None else set()
    own = os.environ.get("HERDR_PANE_ID")
    panes = _herdr("pane", "list")["panes"]
    workspaces, tabs = _herdr("workspace", "list")["workspaces"], _herdr("tab", "list")["tabs"]
    pane = e.get("pane") if any(x.get("pane_id") == e.get("pane") for x in panes) else None
    if pane and (pane in claimed or pane == own or not _shell_only(pane)):
        pane = None  # something else runs there now: a new pane beside it
    saved = (_ASK_MARK.sub("", e.get("workspace") or ""), _ASK_MARK.sub("", e.get("tab") or ""))
    if not pane and all(saved):
        pane = free_pane(panes, workspaces, tabs, saved[0], saved[1], e.get("cwd"), claimed, own)
        if pane and not _shell_only(pane):
            pane = None
    model = e.get("model") or last_model(transcript)
    effort = e.get("effort") or (effort_of(target, model) if model else None)
    bypass = bool(e.get("bypass")) or ran_in_bypass(transcript)
    import of_memory
    mem = e.get("memory_dir") or (of_memory.directory(p) if p and load_policy().get("position_memory") else None)
    work = os.path.join(STATE_DIR, "capacity", "moves", f"{name}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-"
                        f"{'carry' if plan.get('kind') == 'carry' else 'reboot'}")
    note_path = os.path.join(work, "note.md")
    carried = plan.get("kind") == "carry"
    prompt = (f"of_capacity carried this session to another machine. Read {note_path} and follow it." if carried else
              f"The Mac restarted, and of_capacity resumed this session. Read {note_path} and follow it.")
    cmd = launch_command(cwd, target, sid, name, note_path, model=model, effort=effort, bypass=bypass, resumed=True,
                         memory_dir=mem, prompt=prompt, deny=deny_for(p, log), tools=tools_for(p, sid, target, log))
    if home:  # its own workspace; the tab it had there, if it was there (not on the desk)
        ws_label, tab_label = home[0], saved[1] if saved[0] == home[0] and saved[1] else home[1]
    else:
        ws_label, tab_label = saved[0] or "reboot", saved[1] or name
    where = f"pane {pane}" if pane else f"a new pane in {ws_label} / {tab_label}"
    log(f"{name}: resume {sid} on {short(target)} in {where}; {model}, effort {effort}, "
        f"{'bypass' if bypass else 'default'} mode{', own memory' if mem else ''}")
    if pane:
        claimed.add(pane)
    if dry_run:
        log(f"  {cmd}")
        return {"dry_run": True, "command": cmd}
    if not pane:
        cmd_place, follow = place_command(ws_label, tab_label, cwd, workspaces, tabs, panes)
        pane = first_pane_id(_herdr(*cmd_place))
        if not pane:
            raise MoveError(f"herdr {' '.join(cmd_place[:2])} returned no pane")
        if follow:
            tab = next((x.get("tab_id") for x in _herdr("pane", "list")["panes"] if x.get("pane_id") == pane), None)
            if tab:
                _herdr("tab", "rename", tab, follow[1])
        time.sleep(1)
    claimed.add(pane)
    if ensure_herdr_integration(target):
        log(f"  herdr's Claude integration installed in {short(target)}")
    if mem and p and load_policy().get("position_memory"):
        seed_memory([p], only=(p.get("_org"), p["name"]), extra_sessions={sid: (p.get("_org"), p["name"])}, log=log)
    os.makedirs(work, mode=0o700, exist_ok=True)
    with open(note_path, "w") as fh:
        fh.write(carry_note(e, account_of(target), ts, plan) if carried else
                 reboot_note(e, account_of(target), ts, plan.get("prepared_at"), bool(plan.get("tmp_kept"))))
    new = start_session(sid, name, cwd, target, prompt, lambda: _run_in_pane(pane, cmd),
                        lambda: _pane_lines(pane), lambda k: _herdr("pane", "send-keys", pane, k.lower()),
                        "carry: resume note" if carried else "reboot: resume note", log=log)
    if bypass:
        check_bypass_after_start(pane, log)
    of_events.emit("holder_changed", {"position": e.get("position") or name, "previous_session": sid,
                                      "new_session": new["session"],
                                      "evidence": "of capacity carry restore" if carried else "of capacity reboot restore",
                                      "workplace": {"config_dir": target, "account": account_of(target),
                                                    "runtime": "herdr", "pane": pane},
                                      "model": model, "effort": effort,
                                      "trust": {"state": "trusted", "path": trusted_key(target, cwd)}},
                   position=e.get("position") or name, session=new["session"])
    try:
        rename_agent(pane, name, new["session"])
    except MoveError as err:
        log(f"  herdr name: not set ({err}); `of apply` sets it")
    log(f"  resumed: pid {new['pid']}, pane {pane}")
    return {"pane": pane, "session": new["session"], "work": work}


def stray_holder(e, live_dir, args, started_ms, prepared):
    """Pure: why a live copy of a recorded session is not the one the plan recorded, or None when it is. A copy
    started after `prepare` on another account or without bypass is herdr's own resume (2026-11-10: 38 sessions on
    ~/.claude, from stale transcripts, in auto mode); one started before it is a move the plan does not know."""
    if os.path.realpath(live_dir) != os.path.realpath(e["config_dir"]):
        why = f"instead of {short(e['config_dir'])}"
    elif e.get("bypass") and BYPASS_FLAG not in (args or ""):
        why = "without bypass"
    else:
        return None
    if started_ms and started_ms / 1000 < prepared:
        return f"before the restart was prepared ({why})"
    return f"since {_iso(started_ms / 1000) if started_ms else 'the restart'} ({why})"


def reboot_restore(positions, only=None, dry_run=False, log=print, of_bin=None, plan_path=None, place_of=None):
    """Put /private/tmp back and resume every recorded session that is not live, one after another, production
    first. A session that fails is reported and the rest go on; run it again to retry the failed ones.
    place_of(position) -> (workspace label, tab label): where a position's new pane goes when herdr did not bring
    its pane back (its unit's workspace and its system's tab, as `of spawn` places it); `of apply` tidies after."""
    plan = load_reboot_plan(plan_path)
    if not plan:
        raise MoveError(f"no prepared restart in {short(_reboot_current())}: run `of capacity reboot prepare` first")
    keep = os.path.join(os.path.dirname(plan_path) if plan_path else _reboot_current(), "tmp")
    if os.path.isdir(keep) and not dry_run:
        r = put_back_tree(keep, plan.get("tmp_root") or agent_tmp_root())
        log(f"{plan.get('tmp_root')}: {r['restored']} file(s) put back, {r['there']} were still there")
    by_name = {p["name"]: p for p in positions}
    live = {x["session"]: (d, x) for d in config_dirs() for x in live_sessions(d)}
    prepared = calendar.timegm(time.strptime(plan["prepared_at"][:19], "%Y-%m-%dT%H:%M:%S"))
    ts, done, claimed = _iso(time.time()), [], set()
    for e in plan["entries"]:
        if only and e["name"] not in only and (e.get("position") or "") not in only:
            continue
        if e["session"] in live and plan.get("kind") == "carry":  # never stopped for a carry: the original may be
            d, _ = live[e["session"]]                                # this one (a rehearsal's copy coming back)
            log(f"{e['name']}: session {e['session'][:8]} already runs here on {short(d)}; left alone")
            done.append((e["name"], "running", None))
            continue
        if e["session"] in live:
            d, x = live[e["session"]]
            why = stray_holder(e, d, proc_args(x["pid"]), x.get("started"), prepared)
            if why is None:
                log(f"{e['name']}: already running")
                done.append((e["name"], "running", None))
                continue
            if why.startswith("before"):
                log(f"{e['name']}: runs on {short(d)} {why}; left alone (the plan is older than it)")
                done.append((e["name"], "running", None))
                continue
            log(f"{e['name']}: runs on {short(d)} {why}: stopped, to resume as it ran")
            if not dry_run:
                try:
                    os.kill(int(x["pid"]), 15)
                except OSError:
                    pass
                if not _wait(lambda: not _alive(int(x["pid"])), 30, step=1):
                    log(f"{e['name']}: NOT resumed: pid {x['pid']} did not exit")
                    done.append((e["name"], "failed", f"pid {x['pid']} did not exit"))
                    continue
                time.sleep(1)
        try:
            p = by_name.get(e.get("position") or "")
            r = _restore_one(e, p, plan, ts, log=log, dry_run=dry_run, home=place_of(p) if p and place_of else None,
                             claimed=claimed)
            done.append((e["name"], "dry run" if dry_run else "resumed", r))
        except MoveError as err:
            log(f"{e['name']}: NOT resumed: {err}")
            done.append((e["name"], "failed", str(err)))
    if of_bin and not dry_run:
        subprocess.run([sys.executable, of_bin, "apply"], capture_output=True, text=True, timeout=180)
    failed = [n for n, s, _ in done if s == "failed"]
    if not dry_run:
        of_events.emit("capacity_carry_restored" if plan.get("kind") == "carry" else "capacity_reboot_restored",
                       {"resumed": sum(1 for _, s, _ in done if s == "resumed"), "failed": failed}, position=actor())
        if not failed and not only and plan_path is None:  # a restart's own plan; a carry bundle stays as it is
            if plan.get("herdr_resume_before") is not False and "herdr_resume_before" in plan:
                set_herdr_resume(True, log=log)
                log("herdr: resume_agents_on_restore back on")
            os.replace(_reboot_current(), os.path.join(REBOOT_DIR, f"done-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}"))
    log(f"{sum(1 for _, s, _ in done if s == 'resumed')} resumed, {sum(1 for _, s, _ in done if s == 'running')} "
        f"already running, {len(failed)} failed{': ' + ', '.join(failed) if failed else ''}")
    return done


# ------------------------------------------------------------ credentials (broker step 2: inventory)
#
# docs/capacity.md "Credential broker". The inventory holds names, accounts, dates and fingerprints, never a
# value. A login is a config dir signed in by /login. A token (setup-token, API key, Codex auth file) enters by
# `of capacity creds --adopt`: its owner pipes it in on stdin, or gives the fingerprint, and only the first 12
# hex digits of its SHA-256 are kept.

CRED_KINDS = ("setup_token", "api_key", "codex_auth")


def fingerprint(value):
    import hashlib
    return hashlib.sha256(value.strip().encode()).hexdigest()[:12]


def login_rows(dirs=None):
    """One row per config dir: account, plan and rate tier, from the config's oauthAccount (no secrets)."""
    out = []
    for d in dirs if dirs is not None else config_dirs():
        try:
            with open(account_file(d)) as fh:
                oa = json.load(fh).get("oauthAccount") or {}
        except Exception:
            oa = {}
        out.append({"kind": "login", "name": short(d), "account": oa.get("emailAddress"),
                    "plan": oa.get("planDisplayName") or oa.get("organizationType"),
                    "tier": oa.get("organizationRateLimitTier"), "fingerprint": None, "expires_at": None})
    return out


def token_rows(events):
    """Tokens from of.credential_adopted / _minted / _retired events (oldest first), the last word per fingerprint."""
    rows = {}
    for name, p in events:
        fp = p.get("fingerprint")
        if not fp:
            continue
        if name == "of.credential_retired":
            rows.pop(fp, None)
            continue
        rows[fp] = {"kind": p.get("kind"), "name": p.get("name"), "account": p.get("account"), "plan": None,
                    "tier": None, "fingerprint": fp, "expires_at": p.get("expires_at"),
                    "consumer": p.get("consumer"), "since": p.get("at")}
    return list(rows.values())


def creds(conn=None):
    import of_store
    conn = conn or of_store.connect()
    ev = [(n, json.loads(pl or "{}")) for n, pl in conn.execute(
        "SELECT name, payload FROM event WHERE name IN ('of.credential_adopted', 'of.credential_minted', "
        "'of.credential_retired') ORDER BY ts")]
    return login_rows() + token_rows(ev)


def adopt(name, kind, account, expires_at=None, value=None, fp=None, consumer=None):
    """Record a token that exists outside the broker, by fingerprint. `value` (from stdin) is hashed and dropped."""
    if kind not in CRED_KINDS:
        raise MoveError(f"kind must be one of {', '.join(CRED_KINDS)}")
    if bool(value) == bool(fp):
        raise MoveError("give the token on stdin (--from-stdin) or its --fingerprint, not both")
    fp = fingerprint(value) if value else fp.lower()[:12]
    if not _re.fullmatch(r"[0-9a-f]{12}", fp):
        raise MoveError("a fingerprint is the first 12 hex digits of the token's SHA-256")
    payload = {"name": name, "kind": kind, "account": account, "fingerprint": fp, "expires_at": expires_at,
               "consumer": consumer, "at": _iso(time.time())}
    of_events.emit("credential_adopted", payload, position=actor(),
                   session=os.environ.get("CLAUDE_CODE_SESSION_ID"))
    return payload


def store_secret(name, kind, account, consumer=None, read=None, interactive=None, put=None):
    """The founder pastes a value he created elsewhere (a Console API key) into a hidden prompt, in his own terminal;
    it goes straight to the vault by the service account, and only its fingerprint is recorded. Refuses to run from
    automation or an agent's pane, like vault_setup."""
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if os.environ.get("OF_AUTOMATION") or os.environ.get("HERDR_PANE_ID") or not interactive:
        raise MoveError("run this yourself in a normal terminal tab: you paste the value into a hidden prompt")
    if kind not in CRED_KINDS:
        raise MoveError(f"kind must be one of {', '.join(CRED_KINDS)}")
    if read is None:
        import getpass
        read = lambda: getpass.getpass(f"Paste the {kind} for {name} (hidden), then Enter: ")  # noqa: E731
    value = (read() or "").strip()
    if len(value) < 20:
        raise MoveError("nothing usable was pasted; nothing stored")
    fp = fingerprint(value)
    item = (put or vault_put)(f"{kind} {name}", value, {"account": account, "kind": kind, "fingerprint": fp,
                                                         "consumer": consumer})
    rec = {"name": name, "kind": kind, "account": account, "fingerprint": fp, "vault_item": item, "consumer": consumer,
           "at": _iso(time.time())}
    of_events.emit("credential_adopted", rec, position=actor())
    return rec


def format_creds(rows):
    out = [f"{'kind':<12} {'name':<26} {'account':<38} {'plan':<7} {'fingerprint':<13} {'expires':<11} consumer"]
    for r in rows:
        out.append(f"{r['kind']:<12} {(r.get('name') or '-')[:26]:<26} {(r.get('account') or '?')[:38]:<38} "
                   f"{(r.get('plan') or '-')[:7]:<7} {(r.get('fingerprint') or '-'):<13} "
                   f"{(r.get('expires_at') or '-')[:10]:<11} {r.get('consumer') or '-'}")
    return "\n".join(out)


# ------------------------------------------------------------ broker step 3: the vault, by the service account only
#
# docs/capacity.md "Store". `op` runs only with OP_SERVICE_ACCOUNT_TOKEN from the mode-600 file, so it never
# reaches the founder's 1Password app (AGENTS.md). A value goes in and out on stdin/stdout, never on a command
# line. This committed module is the only reader of that file (RFC 0003 §1a: only committed scripts read it).

VAULT = "OF Capacity"
SA_TOKEN_FILE = os.path.expanduser("~/.config/of/capacity/op-sa.token")


def _op_env(path=None):
    path = path or SA_TOKEN_FILE
    if not os.path.isfile(path):
        raise MoveError(f"no service-account token at {path}: the founder's one-line vault task is not done yet")
    if os.stat(path).st_mode & 0o077:
        raise MoveError(f"{path} is readable by others: it must be mode 600")
    with open(path) as fh:
        tok = fh.read().strip()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OP_", "HERDR_"))}
    env.update(OP_SERVICE_ACCOUNT_TOKEN=tok, OP_BIOMETRIC_UNLOCK_ENABLED="false", SSH_AUTH_SOCK="", OF_AUTOMATION="1")
    return env


def op(*args, stdin=None, run=subprocess.run, token_file=None):
    out = run(["op", *args], input=stdin, capture_output=True, text=True, env=_op_env(token_file), timeout=60)
    if out.returncode != 0:
        raise MoveError(f"op {' '.join(args[:2])}: {(out.stderr or '').strip()[:200]}")
    return out.stdout


OP_ACCOUNT = None  # bin/of passes it on; the account is capacity.yaml `op_account` (vault_account)


def vault_account(policy=None):
    """The 1Password account (its sign-in address) that holds the vault: capacity.yaml `op_account`, the owner's
    business account, never his personal one."""
    return str((policy if policy is not None else load_policy()).get("op_account") or "").strip() or None


def vault_setup(op_account=None, run=subprocess.run, interactive=None):
    """The founder's one act for the vault, run by him in his own terminal: create "OF Capacity" and the service
    account of-capacity (read and write on that vault only), its token written straight to the mode-600 file.
    It uses his own `op` session (Touch ID), so it refuses to run from automation or an agent's pane."""
    interactive = sys.stdin.isatty() if interactive is None else interactive
    if os.environ.get("OF_AUTOMATION") or os.environ.get("HERDR_PANE_ID") or not interactive:
        raise MoveError("run this yourself in a normal terminal tab: it uses your own 1Password (Touch ID)")
    folder = os.path.dirname(SA_TOKEN_FILE)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    if os.path.exists(SA_TOKEN_FILE):
        raise MoveError(f"{SA_TOKEN_FILE} exists already: the vault is set up (check: of capacity vault)")
    op_account = op_account or vault_account()
    if not op_account:
        raise MoveError("name the 1Password account that holds the vault: capacity.yaml op_account "
                        "(its sign-in address, for example acme.1password.com)")
    have = run(["op", "vault", "get", VAULT, "--account", op_account], capture_output=True, text=True)
    if have.returncode != 0:
        made = run(["op", "vault", "create", VAULT, "--account", op_account], capture_output=True, text=True)
        if made.returncode != 0:
            raise MoveError(f"op vault create: {(made.stderr or '').strip()[:200]}")
    sa = run(["op", "service-account", "create", "of-capacity", "--account", op_account,
              "--vault", f"{VAULT}:read_items,write_items", "--raw"], capture_output=True, text=True)
    if sa.returncode != 0 or not sa.stdout.strip():
        raise MoveError(f"op service-account create: {(sa.stderr or '').strip()[:200]}")
    with open(os.open(SA_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as fh:
        fh.write(sa.stdout.strip() + "\n")
    return SA_TOKEN_FILE


def vault_check(run=subprocess.run, token_file=None):
    """The service account sees exactly one vault, OF Capacity."""
    who = json.loads(op("whoami", "--format", "json", run=run, token_file=token_file) or "{}")
    vaults = [v.get("name") for v in json.loads(op("vault", "list", "--format", "json", run=run,
                                                    token_file=token_file) or "[]")]
    sa = str(who.get("user_type") or who.get("type") or "").upper() == "SERVICE_ACCOUNT"
    return {"service_account": sa, "vaults": vaults, "ok": sa and vaults == [VAULT]}


def vault_put(title, value, fields, run=subprocess.run, token_file=None):
    """Create one item: the value as a concealed field (on stdin), the rest as text. Returns the item id."""
    item = {"title": title, "category": "API_CREDENTIAL",
            "fields": [{"id": "credential", "label": "credential", "type": "CONCEALED", "value": value}]
            + [{"label": k, "type": "STRING", "value": str(v)} for k, v in fields.items() if v is not None]}
    out = op("item", "create", "--vault", VAULT, "--format", "json", "-", stdin=json.dumps(item), run=run,
             token_file=token_file)
    return json.loads(out).get("id")


def vault_read(item_id, run=subprocess.run, token_file=None):
    """A value, for the broker to pipe into an owner's install script; never printed."""
    return op("read", f"op://{VAULT}/{item_id}/credential", run=run, token_file=token_file).strip()


# ------------------------------------------------------------ broker step 4: minting a setup-token
#
# The founder's one act is the browser's Authorize. `claude setup-token` runs under a pseudo-terminal; its token is
# taken from the output and never shown. A setup-token cannot say whose it is, and the browser may be signed in to
# another account, so a probe with the token alone (an empty config dir) must show the same window resets as the
# account's own probe. Only then is it stored.

TOKEN_RE = _re.compile(r"sk-ant-oat01-[A-Za-z0-9_\-]{20,}")
SETUP_TOKEN_TIMEOUT_S = 600


def capture_token(text):
    """The setup-token in a terminal transcript (ANSI and line wraps removed), or None."""
    plain = ANSI.sub("", text or "").replace("\r", "")
    joined = _re.sub(r"\n(?=[A-Za-z0-9_\-])", "", plain)  # a long token may wrap at the terminal's width
    m = TOKEN_RE.search(joined) or TOKEN_RE.search(plain)
    return m.group(0) if m else None


def same_account(mine, theirs):
    """Two probe readings are one account when every window both report resets at the same time."""
    a, b = (mine or {}).get("windows") or {}, (theirs or {}).get("windows") or {}
    common = [w for w in ("seven_day", "five_hour")
              if (a.get(w) or {}).get("resets_at") and (b.get(w) or {}).get("resets_at")]
    return bool(common) and all(a[w]["resets_at"] == b[w]["resets_at"] for w in common)


def _setup_token_pty(d, log=print, timeout=SETUP_TOKEN_TIMEOUT_S):
    """Run `claude setup-token` for config dir `d` under a pseudo-terminal; return the token, never printing it."""
    import pty
    import select
    master, slave = pty.openpty()
    cwd = os.path.join(STATE_DIR, "capacity", "probe")
    os.makedirs(cwd, mode=0o700, exist_ok=True)
    proc = subprocess.Popen([claude_bin(), "setup-token"], stdin=slave, stdout=slave, stderr=slave, env=probe_env(d),
                            cwd=cwd, close_fds=True)
    os.close(slave)
    buf, end, said, last = "", time.time() + timeout, False, time.time()
    try:
        while time.time() < end:
            r, _, _ = select.select([master], [], [], 0.5)
            if r:
                try:
                    chunk = os.read(master, 4096).decode("utf-8", "replace")
                except OSError:
                    break
                buf, last = buf + chunk, time.time()
                if not said and "http" in buf:
                    log(f"  {short(d)}: the browser is open; check that the page shows {account_of(d)}, then Authorize")
                    said = True
            elif proc.poll() is not None:
                break
            if capture_token(buf) and time.time() - last > 1.5:
                break  # the token is complete once the output has been quiet for 1.5 s (it may come in chunks)
        return capture_token(buf)
    finally:
        if proc.poll() is None:
            proc.terminate()
        os.close(master)


def token_probe(token, runner=subprocess.run):
    """A probe that can only authenticate by `token`: an empty config dir, CLAUDE_CODE_OAUTH_TOKEN set."""
    import tempfile
    base = os.path.join(STATE_DIR, "capacity")
    os.makedirs(base, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=base) as empty:
        env = probe_env(empty)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        env.pop("ANTHROPIC_API_KEY", None)
        out = runner(probe_command(), cwd=empty, env=env, capture_output=True, text=True, timeout=PROBE_TIMEOUT_S)
        return parse_probe(out.stdout.splitlines(), out.stderr, out.returncode)


def mint(d, log=print, spawn=None, probe_fn=None, token_probe_fn=None, put=None, now=None):
    """Mint, check and store one setup-token for config dir `d`; returns its record (no value)."""
    d = os.path.expanduser(d)
    now = now or time.time()
    account = account_of(d)
    if not account:
        raise MoveError(f"{short(d)} is not signed in")
    token = (spawn or _setup_token_pty)(d, log)
    if not token:
        raise MoveError(f"{short(d)}: no token came back (was Authorize clicked?)")
    fp = fingerprint(token)
    mine, theirs = (probe_fn or probe)(d), (token_probe_fn or token_probe)(token)
    if not same_account(mine, theirs):
        raise MoveError(f"{short(d)}: the new token (fingerprint {fp}) is not {account}'s: its windows differ, so the "
                        f"browser was signed in to another account. Nothing stored. Revoke it at "
                        f"claude.ai/settings/claude-code (minted {_iso(now)[:16]}) and sign the browser in as {account}")
    expires = time.strftime("%Y-%m-%d", time.gmtime(now + 365 * 86400))
    item = (put or vault_put)(f"setup-token {account} {_iso(now)[:10]}", token,
                              {"account": account, "kind": "setup_token", "fingerprint": fp, "expires_at": expires})
    rec = {"name": f"setup-token {account}", "kind": "setup_token", "account": account, "fingerprint": fp,
           "expires_at": expires, "vault_item": item, "at": _iso(now)}
    of_events.emit("credential_minted", rec, position=actor())
    log(f"  {short(d)}: minted and stored, fingerprint {fp}, expires {expires}")
    return rec


def due_accounts(rows, now=None, days=7):
    """Logins with no setup-token, or with one that expires within `days`."""
    now = now or time.time()
    soon = time.strftime("%Y-%m-%d", time.gmtime(now + days * 86400))
    have = {r["account"] for r in rows if r["kind"] == "setup_token" and (r.get("expires_at") or "9999") > soon}
    return [r for r in rows if r["kind"] == "login" and r.get("account") and r["account"] not in have]


# ------------------------------------------------------------ loops: babysitting (RFC 0005 §8 step 5)
#
# docs/capacity.md "Loops". Babysitting = a response whose tool calls are all runner commands or monitor
# handling, or any response in a turn woken by a monitor or background-task notification. Its cost is mostly the
# doer's context re-read on every wake-up, so the lever is a small-context babysitter, not a model switch.

MODEL_PRICES = {  # $ per million tokens at list: input, output, cache write 5 min, cache write 1 h, cache read
    "claude-opus-5-5": (4, 20, 5, 8, .20), "claude-fable-5-1": (10, 50, 12.5, 20, .25),
    "claude-sonnet-5-5": (2, 10, 2.5, 4, .20), "claude-haiku-5-5": (.10, .50, .125, .20, .01),
    "claude-opus-4-8": (5, 25, 6.25, 10, .50)}
RUNNER_BASE = r"\bkubectl\b"  # an organization adds its own wrappers and names: capacity.yaml `runner_commands`


def runner_cmd(policy=None):
    """Runner commands: kubectl, plus capacity.yaml `runner_commands` (regex fragments, for example kx\\.sh or
    -n <namespace>\\b)."""
    extra = [str(x) for x in ((policy if policy is not None else load_policy()).get("runner_commands") or [])]
    return _re.compile("|".join([RUNNER_BASE] + extra))


MONITOR_TOOLS = {"Monitor", "TaskStop", "TaskOutput", "BashOutput", "KillShell"}
BABYSITTER_CONTEXT = 30_000  # tokens a small babysitter re-reads per response
BABYSIT_MODEL = "claude-sonnet-5-5"


def model_usd(model, u, prices=None):
    p = (prices or MODEL_PRICES).get(model)
    if not p:
        return 0.0
    cc = u.get("cache_creation") or {}
    w1 = cc.get("ephemeral_1h_input_tokens", 0) if cc else u.get("cache_creation_input_tokens", 0)
    w5 = cc.get("ephemeral_5m_input_tokens", 0)
    return (u.get("input_tokens", 0) * p[0] + u.get("output_tokens", 0) * p[1] + w5 * p[2] + w1 * p[3]
            + u.get("cache_read_input_tokens", 0) * p[4]) / 1e6


def babysit_measure(paths, since, until, babysit_model=BABYSIT_MODEL, policy=None):
    """Spend and babysitting share of one session's transcripts (main and subagents) in [since, until)."""
    msgs = {}
    runner = runner_cmd(policy)
    for path in paths:
        turn = "prompt"
        try:
            fh = open(path, errors="replace")
        except OSError:
            continue
        with fh:
            for ln in fh:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                m = r.get("message") or {}
                if r.get("type") == "user":
                    c = m.get("content")
                    if isinstance(c, str):
                        txt, result = c, False
                    else:
                        c = c if isinstance(c, list) else []
                        txt = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
                        result = any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c)
                    if not result and txt.strip():
                        turn = "monitor" if "<task-notification>" in txt else "prompt"
                    continue
                ts = r.get("timestamp") or ""
                if r.get("type") != "assistant" or not m.get("id") or not (since <= ts[:len(since)] and ts < until):
                    continue
                e = msgs.setdefault(m["id"], {"model": m.get("model"), "usage": m.get("usage") or {}, "tools": [],
                                              "turn": turn, "seen": set()})
                for x in m.get("content") or []:
                    if isinstance(x, dict) and x.get("type") == "tool_use":
                        n = x.get("name")
                        cmd = (x.get("input") or {}).get("command", "") if n == "Bash" else ""
                        key = x.get("id") or (n, cmd)
                        if key in e["seen"]:
                            continue  # the same block written twice
                        e["seen"].add(key)
                        e["tools"].append("runner" if n == "Bash" and runner.search(cmd or "")
                                          else "monitor" if n in MONITOR_TOOLS else n)
    out = {"responses": len(msgs), "usd": 0.0, "babysit_usd": 0.0, "monitor_turn_usd": 0.0, "runner_only_usd": 0.0,
           "runner_commands": 0, "monitor_calls": 0, "by_model": {}, "babysit_by_model": {},
           "same_context_estimate_usd": 0.0, "small_context_estimate_usd": 0.0}
    sp = MODEL_PRICES[babysit_model]
    for mid, e in msgs.items():
        u, c = e["usage"], model_usd(e["model"], e["usage"])
        out["usd"] += c
        out["by_model"][e["model"]] = out["by_model"].get(e["model"], 0.0) + c
        out["runner_commands"] += e["tools"].count("runner")
        out["monitor_calls"] += e["tools"].count("monitor")
        runner_only = bool(e["tools"]) and all(t in ("runner", "monitor") for t in e["tools"])
        if runner_only:
            out["runner_only_usd"] += c
        if e["turn"] == "monitor":
            out["monitor_turn_usd"] += c
        if runner_only or e["turn"] == "monitor":
            out["babysit_usd"] += c
            out["babysit_by_model"][e["model"]] = out["babysit_by_model"].get(e["model"], 0.0) + c
            out["same_context_estimate_usd"] += model_usd(babysit_model, u)
            out["small_context_estimate_usd"] += (u.get("input_tokens", 0) * sp[0] + u.get("output_tokens", 0) * sp[1]
                                                  + BABYSITTER_CONTEXT * sp[4]) / 1e6
    for k in ("by_model", "babysit_by_model"):
        out[k] = {m: round(v, 6) for m, v in out[k].items()}
    return out


LOOP_COST_SETTLE_S = 3600  # a session's records reach the store at its Stop: cost a run an hour after it ends


def loop_costs(conn, now, emit=None):
    """For each of.loop_run without an of.loop_cost (RFC 0005 §5 "cost per cycle"): the recording session's spend
    from the run's start (started_at, else queued_at) to its end, each response once by message.id, subagents
    included, at list price. No start: usd None (unknown, never zero). Returns the number of costs written."""
    emit = emit or (lambda k, p: of_events.emit(k, p, position=actor()))
    done = {json.loads(p or "{}").get("run") for (p,) in conn.execute(
        "SELECT payload FROM event WHERE name = 'of.loop_cost'")}
    n = 0
    for eid, ts, sid, pl in conn.execute("SELECT event_id, ts, session_id, payload FROM event WHERE name = 'of.loop_run' "
                                         "ORDER BY ts").fetchall():
        p = json.loads(pl or "{}")
        run = p.get("run") or eid
        end = p.get("ended_at") or ts
        if run in done or not sid or now - (_ts(end) or now) < LOOP_COST_SETTLE_S:
            continue
        start = p.get("started_at") or p.get("queued_at")
        rec = {"work": p.get("work"), "run": run, "rung": p.get("rung"), "hypothesis": p.get("hypothesis"),
               "session": sid, "from": start, "to": end, "usd": None, "fresh_tokens": None, "responses": None,
               "by_model": {}}
        if start:
            by, usd, fresh, k = {}, 0.0, 0, 0
            for model, i, o, w5, w1, w, cr in conn.execute(
                    "SELECT max(model), max(input_tokens), max(output_tokens), max(cache_write_5m_tokens), "
                    "max(cache_write_1h_tokens), max(cache_write_tokens), max(cache_read_tokens) FROM transcript_own "
                    "WHERE session_id = ? AND type = 'assistant' AND timestamp >= ? AND timestamp < ? "
                    "AND message_id IS NOT NULL GROUP BY message_id", (sid, start, end)):
                w1 = w1 or (0 if w5 else (w or 0))
                c = model_usd(model, {"input_tokens": i or 0, "output_tokens": o or 0, "cache_read_input_tokens": cr or 0,
                                      "cache_creation": {"ephemeral_5m_input_tokens": w5 or 0,
                                                         "ephemeral_1h_input_tokens": w1}})
                usd, fresh, k = usd + c, fresh + (i or 0) + (o or 0) + (w5 or 0) + w1, k + 1
                by[model] = round(by.get(model, 0.0) + c, 6)
            rec.update(usd=round(usd, 6), fresh_tokens=fresh, responses=k, by_model=by)
        emit("loop_cost", rec)
        done.add(run)
        n += 1
    return n


def session_paths_for(sid):
    """The fullest copy of a session's transcript (it may exist on several accounts) and its subagents."""
    hits = [p for d in config_dirs() for p in glob.glob(os.path.join(d, "projects", "*", f"{sid}*.jsonl"))]
    if not hits:
        return []
    main = max(hits, key=os.path.getsize)
    return [main] + glob.glob(os.path.join(main[:-6], "subagents", "*.jsonl"))


# ------------------------------------------------------------ plan, watch

THROUGHPUT_ORDER = {"raise": 0, "sustain": 1, "coordinate": 2, "measure": 3, "expense": 4}


def exhausted(row):
    return bool(row.get("logged_in")) and (row.get("status") == "rejected" or any(
        (w.get("utilization") or 0) >= 100 for w in (row.get("windows") or {}).values()))


def plan(rows, positions, sessions=None):
    """For every account past a threshold with positions on it: the target with the most headroom and
    the order to move them in. Production (throughput `raise`, then `sustain`) first, then parked, then
    finished positions; busy holders after the rest (they move between turns). Returns [{from, to, position, status, command}]."""
    by_name = {p["name"]: p for p in positions}
    out = []
    for r in rows:
        if not r.get("positions") or not (over_threshold(r) or exhausted(r)):
            continue
        t = best_target(rows, exclude=r["config_dir"])
        busy = {x.get("session") for x in (sessions or {}).get(r["config_dir"], []) if x.get("status") == "busy"}

        pol = load_policy()

        def key(n):
            p = by_name.get(n) or {}
            sess = ((p.get("holder") or {}).get("session") or "").strip()
            return (sess in busy, {"parked": 1, "finished": 2}.get(p.get("status"), 0),
                    priority_tier(p, pol), THROUGHPUT_ORDER.get(p.get("throughput"), 5), n)
        docs = tool_docs()
        for n in sorted(r["positions"], key=key):
            p = by_name.get(n) or {}
            sess = ((p.get("holder") or {}).get("session") or "").strip()
            tp, unsigned = t, []
            used = {x for x in tools_used(find_transcript(r["config_dir"], sess) or "") if x.startswith("plugin:")} \
                if sess else set()

            def gone(d, n=n, used=used):  # plugins it used and bound tools not signed in there (docs/tools.md §5)
                return (sorted(lacking(used, account_tools(None, plugins_ready(d)))) if used else []) + \
                    [i for i, _ in (missing_signins(n, d, docs) if docs else [])]
            if t and gone(t["config_dir"]):
                unsigned = gone(t["config_dir"])
                tp = best_target(rows, exclude=r["config_dir"], ok=lambda d, gone=gone: not gone(d))
            out.append({"from": r["config_dir"], "to": tp["config_dir"] if tp else None, "position": n,
                        "org": p.get("_org"), "throughput": p.get("throughput"), "status": p.get("status"),
                        "busy": sess in busy, **({"not_signed_in": {t["config_dir"]: unsigned}} if unsigned else {}),
                        "command": f"of capacity move {n} --to {short(tp['config_dir'])}" if tp else None})
    return out


def proposals(rows, proposed, now=None, policy=None):
    """Accounts past a threshold, with positions on them, not yet proposed for this window and reset.
    `proposed` is the set of keys already asked; returns [(key, row, reason, target)]. A 5-hour window that reopens
    within relief_wait_min is not asked about: it reopens on its own, and the balancer waits it out (2026-11-07:
    two such asks reached the founder and were stale within minutes)."""
    now = now or time.time()
    wait_s = (policy or load_policy()).get("relief_wait_min", 90) * 60
    out = []
    for r in rows:
        why = over_threshold(r)
        if not why or not r.get("positions"):
            continue
        w = why.split()[0]
        reset = _ts(((r.get("windows") or {}).get(w) or {}).get("resets_at") or "")
        if w == "five_hour" and reset and reset - now <= wait_s:
            continue
        k = f"{r['config_dir']}|{w}|{((r.get('windows') or {}).get(w) or {}).get('resets_at')}"
        if k in proposed:
            continue
        out.append((k, r, why, best_target(rows, exclude=r["config_dir"])))
    return out


def ask_text(row, why, target):
    w = why.split()[0]
    reset = _local(((row.get("windows") or {}).get(w) or {}).get("resets_at"), True)
    names = row.get("positions") or []
    by_org = row.get("by_org") or {}
    who = "; ".join(f"{o}: {len(ns)}" for o, ns in sorted(by_org.items())) if by_org else ", ".join(names)
    from of_locale import tr
    t = (tr("Most headroom: {dir} ({account}, {free}% free).", dir=short(target["config_dir"]),
            account=target.get("account"), free=headroom(target))
         if target else tr("No other logged-in account has headroom."))
    parts = why.split()
    window = {"five_hour": tr("5-hour window|spent of"), "seven_day": tr("week|spent of")}.get(w, w)
    return tr("Move the positions off the account {dir} ({account}) to another one? It has spent {used} of its "
              "{window} (threshold {limit}), reset {reset}; when the limit runs out, its positions stop in the middle "
              "of their work. Positions on it: {n} ({who}). {target} A move happens between turns, in the same pane, "
              "with its context; the order: `of capacity plan`.",
              dir=short(row["config_dir"]), account=row.get("account"), used=parts[1] if len(parts) > 1 else "?",
              window=window, limit=parts[-1], reset=reset, n=len(names), who=who, target=t)


def stuck_task(position, err):
    """The owner's task when a failed move could not put a pane back (MoveError.stuck)."""
    from of_locale import tr
    pane = _re.search(r"\((w[^)]*|zellij [^)]*)\)", str(err))
    where = pane.group(1) if pane else tr("its pane")
    return tr("Close the exit dialog in the pane of {position} ({where})\n"
              "The balancer of_capacity was moving {position} to another account, its exit did not finish, and it "
              "could not close Claude Code's dialog \"Background work is running\". While the dialog is open the "
              "position stands still, and messages typed into its pane are lost.\n"
              "1. `of go {position}`.\n"
              "2. In the dialog press Esc or choose \"3. Stay\". Do not choose \"Move to background and exit\": the "
              "session would go to the background without a pane.\n"
              "3. Tell of_capacity it is done; it checks the pane and finishes the move later.",
              position=position, where=where)


def watch(positions, ask, log=print, **kw):
    """The `of pulse` hook: read capacity (probing at most every PROBE_EVERY_MIN minutes) and ask the
    owner once per account, window and reset when a position's account passes a threshold."""
    rows = status(positions, **kw)
    cache = load_cache()
    done = set(cache.get("_proposed") or [])
    asked = []
    for k, r, why, t in proposals(rows, done):
        ask(ask_text(r, why, t))
        done.add(k)
        asked.append(k)
        log(f"capacity: asked the owner about {short(r['config_dir'])} ({why})")
    if asked:
        cache = load_cache()
        cache["_proposed"] = sorted(done)
        save_cache(cache)
    return asked


# ------------------------------------------------------------------ close
#
# A closure is confirmed only by the processes (of_architect, 2026-11-06): no live session file, no
# entry in `claude agents --json` of any config dir, no process whose arguments name the session.
# A registry entry saying "closed" is a decision, not a fact.

LIMIT_DIALOG = "Stop and wait for limit to reset"
AUTO_CONTINUE = _re.compile(r"[Cc]ontinuing automatically at")  # "Usage limit reached · continuing automatically at …"


def without_auto_continue(lines):
    """The screen without the limit wait's notice ("continuing automatically at …, Esc to cancel"). A real move
    presses Esc on it first; a dry run does not, and its "Esc to cancel" read as a dialog (2026-11-09: of_platform
    stalled on mail's 5-hour window, `move --dry-run` said "no input box visible")."""
    return [x for x in lines if not AUTO_CONTINUE.search(ANSI.sub("", x))]


def limit_cursor(lines):
    """True when the harness's limit dialog is open with the cursor on "Stop and wait for limit to reset"."""
    plain = [ANSI.sub("", x) for x in lines]
    return any(OPTION.match(x) and LIMIT_DIALOG in x for x in plain)


def agents_json(d):
    env = probe_env(d)
    try:
        out = subprocess.run([claude_bin(), "agents", "--json"], env=env, capture_output=True, text=True, timeout=60)
        return json.loads(out.stdout or "[]")
    except Exception:
        return []


def daemon_up(d):
    """Background sessions run under the config dir's daemon; its log's last line says when it stopped.
    With the daemon down, a `claude agents` record of a background session is a leftover, not a process."""
    try:
        with open(os.path.join(d, "daemon.log"), "rb") as fh:
            fh.seek(max(0, os.path.getsize(fh.name) - 4000))
            last = [x for x in fh.read().decode("utf-8", "replace").splitlines() if x.strip()][-1]
        return "shutting down" not in last
    except Exception:
        return False


def session_traces(sid, dirs=None):
    """Everything that shows the session still running: [(where, pid or job id, detail)]."""
    out = []
    for d in dirs if dirs is not None else config_dirs():
        for s in live_sessions(d):
            if s["session"] == sid:
                out.append((short(d), s["pid"], f"session file, {s['status']}"))
        for a in agents_json(d):
            if a.get("sessionId") == sid and a.get("pid") is None and daemon_up(d):
                out.append((short(d), a.get("id"), f"background agent, {a.get('state') or a.get('status')}"))
    try:
        ps = subprocess.run(["ps", "-axo", "pid=,ppid=,args="], capture_output=True, text=True, timeout=10).stdout
        rows = {}
        for ln in ps.splitlines():
            parts = ln.split(None, 2)
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                rows[int(parts[0])] = (int(parts[1]), parts[2])
        mine, pid = set(), os.getpid()  # this check and the shells that started it name the session too
        while pid and pid not in mine:
            mine.add(pid)
            pid = rows.get(pid, (0, ""))[0]
        for pid, (_, args) in rows.items():
            if pid not in mine and sid in args and "claude" in args and "of capacity" not in args:
                out.append(("ps", pid, args[:80]))
    except Exception:
        pass
    seen, uniq = set(), []
    for t in out:
        if t[1] not in seen:
            seen.add(t[1])
            uniq.append(t)
    return uniq


def close(sid, position, reason, dry_run=False, log=print):
    """Close one live session between turns, with no resume, and confirm it by the processes."""
    found = None
    for d in config_dirs():
        for s in live_sessions(d):
            if s["session"] == sid:
                found = (d, s)
        for a in agents_json(d):
            if a.get("sessionId") == sid and a.get("pid") is None and not found and daemon_up(d):
                found = (d, {"pid": None, "job": a.get("id"), "status": a.get("state"), "kind": "background"})
    if not found:
        traces = session_traces(sid)
        if traces:
            raise MoveError(f"{position}: {sid[:8]} has traces but no session record: {traces}")
        stale = [short(d) for d in config_dirs() if any(a.get("sessionId") == sid for a in agents_json(d))]
        note = f"; a leftover background record in {', '.join(stale)} (its daemon is down)" if stale else ""
        log(f"{position}: {sid[:8]} is not running anywhere (checked session files, claude agents, ps){note}")
        if not dry_run:
            of_events.emit("holder_changed", {"position": position, "previous_session": sid, "new_session": None,
                                              "evidence": f"closure: {reason}; no process found",
                                              "verified": {"checked": ["session files", "claude agents --json", "ps"],
                                                           "leftover_record": stale}},
                           position=position, session=sid)
        return {"position": position, "session": sid, "closed": True, "already": True}
    d, s = found
    if s.get("status") == "busy":
        raise MoveError(f"{position}: busy; close it between turns")
    if s.get("kind") == "background":
        log(f"{position}: background agent {s['job']} on {short(d)} ({s['status']}): claude stop")
        if dry_run:
            return {"dry_run": True}
        subprocess.run([claude_bin(), "stop", s["job"]], env=probe_env(d), capture_output=True, text=True, timeout=60)
    else:
        env = proc_env(s["pid"])
        a = _agent_by_session(sid)
        if a:
            lines = lambda: _pane_lines(a["pane_id"])  # noqa: E731
            keys = lambda k: _herdr("pane", "send-keys", a["pane_id"], k.lower())  # noqa: E731
            chars = lambda t: _herdr("agent", "prompt", a["pane_id"], t)  # noqa: E731
            where = a["pane_id"]
        elif env.get("ZELLIJ_PANE_ID"):
            zs, zp = env["ZELLIJ_SESSION_NAME"], env["ZELLIJ_PANE_ID"]
            lines = lambda: _zellij(zs, "dump-screen", "--pane-id", zp, "--ansi").splitlines()[-60:]  # noqa: E731
            keys = lambda k: _zellij(zs, "send-keys", "--pane-id", zp, k)  # noqa: E731

            def chars(t):
                _zellij(zs, "write-chars", "--pane-id", zp, t)
                time.sleep(0.3)
                _zellij(zs, "send-keys", "--pane-id", zp, "Enter")
            where = f"zellij {zs}:{zp}"
        else:
            raise MoveError(f"{position}: pid {s['pid']} is in no herdr or Zellij pane; stop it by hand")
        screen = lines()
        log(f"{position}: {short(d)} pid {s['pid']} in {where} ({s['status']})")
        if dry_run:
            log(f"  limit dialog: {any(LIMIT_DIALOG in ANSI.sub('', x) for x in screen)}; draft: {input_draft(screen)!r}")
            return {"dry_run": True}
        if any(LIMIT_DIALOG in ANSI.sub("", x) for x in screen):
            keys("Enter")  # option 1: stop and wait (never the paid one)
            time.sleep(2)
            screen = lines()
        draft = input_draft(screen)
        if draft is None or draft:
            raise MoveError(f"{position}: input box in {where}: {'not visible' if draft is None else repr(draft[:80])}")
        _inject(sid, "/exit", f"closure: {reason}")
        try:  # a closure ends the session, its scheduled tasks with it: "Exit and stop tasks", never background
            exit_session(int(s["pid"]), lambda: chars("/exit"), lines, keys, allow_shells=True, log=log)
        except MoveError as e:
            raise MoveError(f"{position}: {e} ({where})", stuck=e.stuck)
        if where.startswith("zellij"):
            time.sleep(1)
            keys("Esc")  # a held pane drops to a shell
    traces = _wait(lambda: not session_traces(sid), 30, step=2)
    left = session_traces(sid)
    if left:
        raise MoveError(f"{position}: still running after the closure: {left}")
    of_events.emit("holder_changed", {"position": position, "previous_session": sid, "new_session": None,
                                      "evidence": f"closure: {reason}",
                                      "verified": {"pid": s.get("pid"), "job": s.get("job"), "config_dir": d,
                                                   "checked": ["session files", "claude agents --json", "ps"]}},
                   position=position, session=sid)
    log(f"  closed and verified: no session file, no agent entry, no process for {sid[:8]}")
    return {"position": position, "session": sid, "pid": s.get("pid"), "job": s.get("job"), "closed": True}


# ---------------------------------------------------------------- balance
#
# Use the capacity that expires first (founder, 2026-11-06): budget left in a window at its reset is
# lost, so load goes to the account whose weekly budget resets soonest, filling its 5-hour window up
# to `fill_to` by the window's reset, never past it. An account about to stall (5-hour window at
# `relief_at`, or its week used up) sheds idle sessions to the account with the most headroom.
# Only idle holders move (between turns, no background shells), the biggest burners first, and
# the positions in `hold` (production) only by relief, never by fill (founder, ask 44a3b1f9).

PRICES = {"in": 4, "out": 20, "cw5": 5, "cw1h": 8, "cr": 0.2}  # $/MTok at list, Opus 5.5 (measured 2026-10)
POLICY = {"auto": False, "fill_to": 85, "relief_at": 95, "week_max": 97, "min_rate": 5.0, "max_moves": 2,
          "hold": [],  # production positions, named in capacity.yaml `hold`
          "position_memory": False}
# of_capacity is not held (2026-11-08): the balancer runs as its own process, so relief moves of_capacity's session
# between turns like any other; held, it sat on its own limit screen from 11:42Z to 14:34Z with nobody to move it.


def _matches(p, rule):
    """A position matches a priority rule: every key given (org, unit, throughput, name) holds; lists mean any."""
    have = {"org": p.get("_org"), "unit": p.get("_unit"), "throughput": p.get("throughput"), "name": p.get("name")}
    for k, want in (rule or {}).items():
        if have.get(k) not in (want if isinstance(want, list) else [want]):
            return False
    return True


def priority_tier(p, policy):
    """0: first to model capacity, 1: the rest, 2: last. capacity.yaml `priority: {first: [rules], last: [rules]}`
    (founder, 2026-11-07, ask 4576f634: the Acme money side first, throughput raise or measure; the
    platform unit, in keep mode, last). With no rule every position is equal."""
    pr = (policy or {}).get("priority") or {}
    if any(_matches(p, r) for r in pr.get("first") or []):
        return 0
    if any(_matches(p, r) for r in pr.get("last") or []):
        return 2
    return 1


def load_policy():
    p = dict(POLICY)
    try:
        import yaml
        with open(of_events.config_path("capacity.yaml")) as fh:
            p.update(yaml.safe_load(fh) or {})
    except Exception:
        pass
    return p


def write_defaults(settings, path=None, now=None):
    """Merge a package's settings into capacity.yaml (bench/capacity/package.yaml `settings`, for of init's step):
    a key the person already set is kept as it is, a missing one is appended under a dated comment; the file's own
    lines and comments stay. Returns (written, kept): the keys of each. A capacity.yaml that is not a YAML mapping
    raises ValueError and is left alone."""
    import yaml
    path = path or of_events.config_path("capacity.yaml")
    try:
        with open(path) as fh:
            text = fh.read()
    except FileNotFoundError:
        text = ""
    current = yaml.safe_load(text) if text.strip() else {}
    if not isinstance(current, dict):
        raise ValueError(f"{path} is not a YAML mapping; nothing written")
    written = [k for k in settings if k not in current]
    kept = [k for k in settings if k in current]
    if written:
        stamp = _iso(now or time.time())[:10]
        add = (f"# bench/capacity defaults, written by of init {stamp}; a key set above is never overwritten\n"
               + yaml.safe_dump({k: settings[k] for k in written}, sort_keys=False, default_flow_style=False))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(text + ("" if not text or text.endswith("\n") else "\n") + add)
        os.replace(tmp, path)
    return written, kept


def capacity_position(policy=None):
    """'<org>:<name>' of the position that serves this machine's accounts: capacity.yaml `position` (docs/accounts.md
    B4: one per person, in their home organization; of init writes it), or None. With none, the invariant's work
    reaches the owner as a card (owner_card)."""
    p = (policy if policy is not None else load_policy()).get("position")
    return str(p).strip() or None if p else None


def actor(policy=None):
    """The position name the balancer's records and asks carry: the capacity position's, else of_capacity."""
    p = capacity_position(policy)
    return p.split(":", 1)[-1] if p else "of_capacity"


def owner_card(kind, stalls, accepting, spent, numbers):
    """The owner's card for the invariant's work when no capacity position serves the machine: a session stands on
    its limit for two runs ("self"), or the pool is short or spent ("pool", a money question)."""
    from of_locale import tr
    if kind == "self":
        who = ", ".join(sorted({s.get("position") or s["session"] for s in stalls})) or "?"
        return (tr("{who} stand on their usage limit for two balancer runs while {accepting} accept work, and the "
                   "balancer could not move them. No capacity position serves this machine's accounts, so this card "
                   "is the only notice. Ask your conductor to move them, or wait for their accounts to reopen.",
                   who=who, accepting=", ".join(short(a) for a in sorted(accepting)) or "-"), None)
    state = tr("spent|pool") if spent else tr("short|pool")
    return (tr("The Claude accounts on this machine are {state}: sessions had to be moved twice within five hours, or "
               "no account accepts work. Moves of stopped sessions go on by themselves; the choice is money: wait for "
               "the accounts to reopen, or add an account (of account add). Accounts now:\n{numbers}",
               state=state, numbers=numbers or "-"),
            tr("wait for the accounts to reopen") + "|" + tr("add an account"))


def usage_cost(u):
    cc = u.get("cache_creation") or {}
    cw1 = cc.get("ephemeral_1h_input_tokens", 0) if cc else u.get("cache_creation_input_tokens", 0)
    cw5 = cc.get("ephemeral_5m_input_tokens", 0)
    return (u.get("input_tokens", 0) * PRICES["in"] + u.get("output_tokens", 0) * PRICES["out"] + cw1 * PRICES["cw1h"]
            + cw5 * PRICES["cw5"] + u.get("cache_read_input_tokens", 0) * PRICES["cr"]) / 1e6


def _transcripts(d):
    return glob.glob(os.path.join(d, "projects", "*", "*.jsonl")) + \
        glob.glob(os.path.join(d, "projects", "*", "*", "subagents", "*.jsonl"))


def _session_of(path):
    return path.split(os.sep)[-3] if f"{os.sep}subagents{os.sep}" in path else os.path.basename(path)[:-6]


def iter_usage(path, start=0):
    """(message id, epoch, usage) for each API response in a transcript from byte `start`, and the offset of
    the end of the last complete line. Claude Code writes one record per content block of a response, each
    with the same message id and the same usage: a response is counted once, by its message id."""
    out = []
    with open(path, "rb") as fh:
        fh.seek(start)
        data = fh.read()
    end = data.rfind(b"\n") + 1
    for ln in data[:end].decode("utf-8", "replace").splitlines():
        if '"usage"' not in ln:
            continue
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        m = j.get("message")
        if not isinstance(m, dict) or not m.get("usage") or not m.get("id"):
            continue
        out.append((m["id"], _ts(j.get("timestamp") or ""), m["usage"]))
    return out, start + end


SPEND_KEEP_S = 6 * 3600  # covers a 5-hour window and the 2-hour burn rate
_CONFIG_DIR_RE = _re.compile(r"^(.*/\.claude[^/]*)/projects/")


def _spend_path():
    return os.path.join(STATE_DIR, "capacity", "spend.json")


def spend_index(dirs, now=None, keep_s=SPEND_KEEP_S):
    """Incremental: a byte cursor per transcript, a rolling index of responses {message id: [epoch, session,
    $ at list, config dir]} over the last `keep_s` seconds. Each call reads only what was appended."""
    now = now or time.time()
    try:
        with open(_spend_path()) as fh:
            st = json.load(fh)
    except Exception:
        st = {"files": {}, "msgs": {}}
    files, msgs, copied = st["files"], st["msgs"], st.get("copied") or {}
    born = st.get("born") or {}  # session -> birth of its oldest transcript file (a fork's copy is younger)
    for d in dirs:
        for f in _transcripts(d):
            try:
                info = os.stat(f)
            except OSError:
                continue
            if info.st_mtime < now - keep_s:
                continue
            prev = files.get(f)
            start = prev[1] if prev and prev[0] == info.st_ino and prev[1] <= info.st_size else 0
            if start == info.st_size:
                continue
            try:
                recs, end = iter_usage(f, start)
            except OSError:
                continue
            files[f] = [info.st_ino, end]
            sid = _session_of(f)
            b = getattr(info, "st_birthtime", info.st_ctime)
            born[sid] = min(born.get(sid, b), b)
            since_copy = max([t for pfx, t in copied.items() if f == pfx or f.startswith(pfx + os.sep)] or [0])
            sub = f"{os.sep}subagents{os.sep}" in f
            for mid, t, u in recs:
                # a move copies a transcript: what it held before the copy was spent on the old account
                if t and t >= now - keep_s and t >= since_copy:
                    prev_ = msgs.get(mid)
                    # a fork (a side question asked in a session) copies the session's records into its own
                    # transcript under its own id: the response belongs to the transcript born first
                    if prev_ and prev_[1] != sid and born.get(prev_[1], b) <= born[sid]:
                        continue
                    msgs[mid] = [t, sid, usage_cost(u), d, sub]
    msgs = {k: v for k, v in msgs.items() if v[0] >= now - keep_s}
    files = {k: v for k, v in files.items() if os.path.exists(k)}
    copied = {k: v for k, v in copied.items() if v >= now - 7 * 86400}
    live_sids = {v[1] for v in msgs.values()}
    born = {k: v for k, v in born.items() if k in live_sids}
    os.makedirs(os.path.dirname(_spend_path()), mode=0o700, exist_ok=True)
    tmp = f"{_spend_path()}.{os.getpid()}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump({"files": files, "msgs": msgs, "copied": copied, "born": born}, fh)
    os.replace(tmp, _spend_path())
    return msgs


def mark_copied(paths, t=None):
    """Record that these transcript paths (files or session dirs) were copied in at time t: the spend
    index counts their records from t on (the earlier ones belong to the account they were copied from)."""
    t = t or time.time()
    try:
        with open(_spend_path()) as fh:
            st = json.load(fh)
    except Exception:
        st = {"files": {}, "msgs": {}}
    st.setdefault("copied", {}).update({p: t for p in paths})
    m = [_CONFIG_DIR_RE.match(p) for p in paths]
    sessions = {(x.group(1), _session_of(p) if p.endswith(".jsonl") else os.path.basename(p))
                for x, p in zip(m, paths) if x}
    # re-read every transcript of those sessions (main and subagents) under the new rule
    st["files"] = {f: v for f, v in st["files"].items()
                   if not any(f.startswith(d + os.sep) and _session_of(f) == sid for d, sid in sessions)}
    st["msgs"] = {k: v for k, v in st["msgs"].items() if (v[3], v[1]) not in sessions}
    os.makedirs(os.path.dirname(_spend_path()), mode=0o700, exist_ok=True)
    tmp = f"{_spend_path()}.{os.getpid()}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(st, fh)
    os.replace(tmp, _spend_path())


def spend_since(d, t0, index=None):
    """{session id: $ at list} spent in config dir `d` since epoch t0 (from the rolling index)."""
    index = index if index is not None else spend_index([d])
    out = {}
    for v in index.values():
        t, sid, cost, dd = v[0], v[1], v[2], v[3]
        if dd == d and t >= t0:
            out[sid] = out.get(sid, 0.0) + cost
    return out


def usage(dirs=None, since=None, until=None, sessions=None):
    """The canonical token count for any period (a full read; not for the 5-minute pulse). Per session:
    input, output, cache_read, cache_write_5m, cache_write_1h, fresh (input + cache writes + output, the
    tokens the provider processes anew), responses, and cost_usd at list. One API response counts once
    (by message id), subagents count to their session."""
    out, seen = {}, set()
    for d in dirs if dirs is not None else config_dirs():
        for f in _transcripts(d):
            sid = _session_of(f)
            if sessions is not None and sid not in sessions:
                continue
            if since and os.path.getmtime(f) < since:
                continue
            try:
                recs, _ = iter_usage(f, 0)
            except OSError:
                continue
            for mid, t, u in recs:
                if mid in seen or (since and (t or 0) < since) or (until and (t or 0) >= until):
                    continue
                seen.add(mid)
                cc = u.get("cache_creation") or {}
                w1 = cc.get("ephemeral_1h_input_tokens", 0) if cc else u.get("cache_creation_input_tokens", 0)
                w5 = cc.get("ephemeral_5m_input_tokens", 0)
                o = out.setdefault(sid, {"input": 0, "output": 0, "cache_read": 0, "cache_write_5m": 0,
                                         "cache_write_1h": 0, "fresh": 0, "responses": 0, "cost_usd": 0.0})
                o["input"] += u.get("input_tokens", 0)
                o["output"] += u.get("output_tokens", 0)
                o["cache_read"] += u.get("cache_read_input_tokens", 0)
                o["cache_write_5m"] += w5
                o["cache_write_1h"] += w1
                o["fresh"] += u.get("input_tokens", 0) + u.get("output_tokens", 0) + w1 + w5
                o["responses"] += 1
                o["cost_usd"] += usage_cost(u)
    return out


def current_index(dirs, now):
    """The spend index: the listener's store view when it is complete, else the transcript cursor."""
    tr = spend_index(dirs, now)
    try:
        import of_store
        idx = store_index(of_store.connect())
        if idx is not None:
            # a session's records reach the store at its next Stop (a subagent's at its parent's): during a
            # long turn, and for config dirs without the OF hooks, take what the store does not have yet from
            # the cursor (by message id, so nothing counts twice; the store wins where both have it)
            idx.update({k: v for k, v in tr.items() if k not in idx})
            return idx
    except Exception:
        pass
    return tr


def burn_rates(dirs, now, index=None):
    """{session: $/h}: the higher of the last 30 minutes and the last 2 hours. A holder idle right now but
    busy all morning will be busy again; the balancer can only move it while it is idle."""
    index = index if index is not None else current_index(dirs, now)
    short_, long_ = {}, {}
    for d in dirs:  # a session that moved within the window spent on both accounts: its rate is the sum
        for k, v in spend_since(d, now - 1800, index).items():
            short_[k] = short_.get(k, 0.0) + v
        for k, v in spend_since(d, now - 7200, index).items():
            long_[k] = long_.get(k, 0.0) + v
    return {k: max(short_.get(k, 0.0) * 2, long_.get(k, 0.0) / 2) for k in set(short_) | set(long_)}


def calibrate_week(row, now, index=None, hist=None, keep_s=SPEND_KEEP_S, min_delta=3):
    """$ at list per 1% of the account's week. The spend index holds only the last `keep_s` (6 h), so the spend
    there is divided by the week's growth over the same hours, from the probe history `hist` ([epoch, util,
    resets_at], oldest first). Only when the week opened inside the index is the whole week used. 2026-11-09:
    dividing 6 hours of spend by the week since it opened put an Ultra at $5 per 1% instead of about $26, so the
    balancer saw weeks about to run out."""
    w = (row.get("windows") or {}).get("seven_day") or {}
    reset, util = _ts(w.get("resets_at") or ""), w.get("utilization")
    if not reset or util is None:
        return None
    opened = reset - 7 * 86400
    if opened >= now - keep_s:
        if util < 5:
            return None
        spent = sum(spend_since(row["config_dir"], opened, index).values())
        return spent / util if spent else None
    base = next(((t, u) for t, u, r in (hist or []) if t >= now - keep_s + 600 and r == w.get("resets_at")
                 and u is not None and util - u >= min_delta), None)
    if not base:
        return None
    spent = sum(spend_since(row["config_dir"], base[0], index).values())
    return spent / (util - base[1]) if spent else None


WEEK_HIST_KEEP_S = SPEND_KEEP_S + 3600


def week_history(cache, d, now):
    """[epoch, util, resets_at] of the account's week, oldest first, from the probes kept in the cache."""
    return [x for x in ((cache.get("_week_hist") or {}).get(d) or []) if x[0] >= now - WEEK_HIST_KEEP_S]


def calibrate(row, now, index=None):
    """$ at list per 1% of the account's 5-hour window, from what it spent since the window opened."""
    w = (row.get("windows") or {}).get("five_hour") or {}
    reset, util = _ts(w.get("resets_at") or ""), w.get("utilization")
    if not reset or not util or util < 3:
        return None
    spent = sum(spend_since(row["config_dir"], reset - 5 * 3600, index).values())
    return spent / util if spent else None


CONNECTOR = _re.compile(r'"name":\s*"mcp__claude_ai_([A-Za-z0-9_]+?)__')
# a plugin's MCP tools are mcp__plugin_<plugin>_<server>__<tool>; plugins are installed per config dir (2026-11-11:
# ~/.claude-c had none, and aws_keeper lost Cloudflare in a move there with OFF-013's token rotation waiting on it)
PLUGIN = _re.compile(r'"name":\s*"mcp__plugin_([a-z0-9-]+)_')


def connectors_used(transcript, tail=4_000_000):
    """claude.ai connectors (account-level, e.g. Slack) the session called recently, from its transcript tail."""
    return {t for t in tools_used(transcript, tail) if not t.startswith("plugin:")}


def tools_used(transcript, tail=4_000_000):
    """The account-bound tools the session called recently, from its transcript tail: claude.ai connectors by name,
    and plugins as "plugin:<name>". A moved session's copied history counts: it used them, so it needs them."""
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as fh:
            fh.seek(max(0, size - tail))
            data = fh.read().decode("utf-8", "replace")
    except Exception:
        return set()
    return set(CONNECTOR.findall(data)) | {"plugin:" + x for x in PLUGIN.findall(data)}


def plugins_seen(d):
    """The plugins installed and enabled on config dir `d`, as "plugin:<name>" (installed_plugins.json, settings.json
    enabledPlugins). None when unreadable. An installed plugin whose MCP server needs a login (Cloudflare's OAuth is
    kept per config dir) still counts here, since the files cannot tell: plugins_ready() takes those out."""
    d = os.path.expanduser(d)
    try:
        with open(os.path.join(d, "plugins", "installed_plugins.json")) as fh:
            j = json.load(fh)
        installed = set((j.get("plugins") if isinstance(j.get("plugins"), dict) else j) or {})
    except FileNotFoundError:
        installed = set()
    except Exception:
        return None
    try:
        with open(os.path.join(d, "settings.json")) as fh:
            enabled = (json.load(fh) or {}).get("enabledPlugins") or {}
    except FileNotFoundError:
        enabled = {}
    except Exception:
        return None
    return {"plugin:" + k.split("@", 1)[0] for k in installed if enabled.get(k) is not False}


def plugins_ready(d, now=None, run=None):
    """plugins_seen(d) less the plugins whose MCP server `claude mcp list` shows as not connected ("Needs
    authentication", failed): their tools are absent in a session there (2026-11-11: aws_keeper, moved to ~/.claude-d,
    found Cloudflare installed but signed out, only its authenticate tools, with a token deletion waiting on it)."""
    have = plugins_seen(d)
    if have is None:
        return None
    down = plugins_down(d, now, run)
    return have - down if down else have


def account_tools(conn, plug):
    """What an account has, for lacking(): its connectors (None: claude mcp list did not answer) and plugins (None:
    files unreadable), each side marked known only when it was read. None when neither was."""
    if conn is None and plug is None:
        return None
    return (set(plug) | {"plugins:known"} if plug is not None else set()) | \
        (set(conn) | {"connectors:known"} if conn is not None else set())


def lacking(used, have):
    """What of `used` (tools_used) the account lacks, counting only the side that was read (account_tools). A
    plain set of connector names (no markers) is taken as connectors read, plugins unknown."""
    if have is None:
        return set()
    marked = any(h.endswith(":known") for h in have)
    plug_known = "plugins:known" in have
    bound_known = "bound:known" in have  # account-bound tools of the position's profile (docs/tools.md §5)
    conn_known = "connectors:known" in have or not marked
    return {u for u in used if u not in have and (plug_known if u.startswith("plugin:") else
                                                  bound_known if u.startswith("bound:") else conn_known)}


VETO = ("plugin:", "bound:")  # what relief never moves a session without (connectors: the note says)


MCP_LINE = _re.compile(r"^claude\.ai (.+?): \S+ - (.+)$")
MCP_PLUGIN_LINE = _re.compile(r"^plugin:([A-Za-z0-9_-]+):[^:\s]+: (.+)$")
CONNECTORS_MAX_AGE_S = 6 * 3600


def parse_mcp_list(text):
    """The claude.ai connectors `claude mcp list` reports as connected, named as in tool ids (spaces as _)."""
    out = set()
    for line in (text or "").splitlines():
        m = MCP_LINE.match(line.strip())
        if m and "Connected" in m.group(2) and "✘" not in m.group(2):
            out.add(m.group(1).strip().replace(" ", "_"))
    return out


def parse_mcp_servers(text):
    """[(name, url or None, connected)] of every server line of `claude mcp list` ("<name>: <url or command> -
    <state>"); a plugin's server is named "plugin:<plugin>:<server>", a claude.ai connector "claude.ai <Name>"."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if ": " not in line or " - " not in line:
            continue
        head, state = line.rsplit(" - ", 1)
        name, target = head.split(": ", 1)
        url = target.split()[0] if target.startswith(("http://", "https://")) else None
        out.append((name, url, "Connected" in state and "✘" not in state))
    return out


def parse_mcp_plugins_down(text):
    """The plugins whose MCP server `claude mcp list` reports as not connected, as "plugin:<name>"."""
    out = set()
    for line in (text or "").splitlines():
        m = MCP_PLUGIN_LINE.match(line.strip())
        if m and "Connected" not in m.group(2).rsplit(" - ", 1)[-1]:
            out.add("plugin:" + m.group(1))
    return out


def plugins_down(d, now=None, run=None):
    """Plugins installed on account `d` whose MCP server is not connected there (parse_mcp_plugins_down), from the
    same `claude mcp list` run and cache as connectors_seen; None if unknown."""
    if connectors_seen(d, now, run) is None:
        return None
    c = (load_cache().get("_connectors") or {}).get(os.path.expanduser(d)) or {}
    return set(c["plugins_down"]) if "plugins_down" in c else None


def connectors_seen(d, now=None, run=None):
    """claude.ai connectors connected on account `d`, from `claude mcp list` (cached 6 h); None if unknown.
    A connector belongs to the claude.ai account, so a moved session finds only the target's (2026-11-07:
    ~/.claude had Slack, Gmail, Google Drive and Calendar; ~/.claude-e had none of them). A transcript cannot tell:
    a moved session's copied history shows calls made on the old account."""
    now = now or time.time()
    d = os.path.expanduser(d)
    cache = load_cache()
    c = (cache.get("_connectors") or {}).get(d)
    if c and "servers" in c and now - c.get("at", 0) < CONNECTORS_MAX_AGE_S:
        return set(c.get("names") or [])
    probe_dir = os.path.join(STATE_DIR, "capacity", "probe")
    os.makedirs(probe_dir, mode=0o700, exist_ok=True)
    try:
        if run:
            text = run(d)
        else:
            text = subprocess.run([claude_bin(), "mcp", "list"], env=probe_env(d), cwd=probe_dir, capture_output=True,
                                  text=True, timeout=120).stdout
    except Exception:
        return None
    names = parse_mcp_list(text)
    cache = load_cache()
    cache.setdefault("_connectors", {})[d] = {"at": now, "names": sorted(names),
                                              "plugins_down": sorted(parse_mcp_plugins_down(text)),
                                              "servers": [list(x) for x in parse_mcp_servers(text)]}
    save_cache(cache)
    return names


def mcp_servers(d, now=None, run=None):
    """parse_mcp_servers of account `d`, from the same run and cache as connectors_seen; None if unknown."""
    if connectors_seen(d, now, run) is None:
        return None
    c = (load_cache().get("_connectors") or {}).get(os.path.expanduser(d)) or {}
    return [tuple(x) for x in c["servers"]] if "servers" in c else None


# ------------------------------------------------------------ account-bound tools (docs/tools.md §5)
#
# A position's tools are declared in its organization's `tools:` catalogue (of_tools, hand-over 9843194a). Those
# whose sign-in lives in one Claude account (an OAuth plugin, a claude.ai connector) work only where someone signed
# in: fill, relief, plan and a move by hand go only to such an account; rescue prefers one and moves anyway (a
# stopped session cannot work at all). A missing sign-in is the founder's task, one per account.

def api_online():
    """Whether the API answers a TCP connect (of_net.online, cached a minute); True when that cannot be told. A move
    resumes the session on the new account: over a dropped link the resume can leave it dead (2026-11-11)."""
    try:
        import of_net
        return bool(of_net.online())
    except Exception:  # noqa: BLE001
        return True


def tool_docs():
    """{org: registry doc} of the organizations with a `tools:` catalogue; {} when none or unreadable."""
    try:
        import of_tools
        return {o: d for o, d in of_tools.registries().items() if (d or {}).get("tools")}
    except Exception:  # noqa: BLE001 - no catalogue reader: nothing is account-bound
        return {}


def bound_tools(position_name, docs=None):
    """[(org/id, catalogue entry)] of the position's account-bound tools (of_tools.session_config); [] without a
    catalogue."""
    docs = tool_docs() if docs is None else docs
    if not docs or not position_name:
        return []
    try:
        import of_tools
        org, pos, doc = of_tools.find_position(position_name, docs)
        ids = of_tools.session_config(pos, doc, None, installed=[])["account_bound"]
    except Exception:  # noqa: BLE001 - not in a catalogued organization
        return []
    return [(f"{org}/{i}", doc["tools"][i]) for i in ids if isinstance(doc["tools"].get(i), dict)]


def signed_in(entry, d, now=None, run=None):
    """Whether account-bound tool `entry` is connected on account `d` (`claude mcp list`, by its url; a plugin
    without one by plugins_ready); None when unknown, which never vetoes."""
    url = str(entry.get("url") or "").rstrip("/")
    if url:
        servers = mcp_servers(d, now, run)
        if servers is None:
            return None
        return any(u and u.rstrip("/") == url and ok for _, u, ok in servers)
    if entry.get("kind") == "plugin" and entry.get("plugin"):
        have = plugins_ready(d, now, run)
        return None if have is None else "plugin:" + str(entry["plugin"]).split("@", 1)[0] in have
    return None


def missing_signins(position_name, d, docs=None, now=None, run=None):
    """[(org/id, entry)] of the position's account-bound tools not signed in on account `d`."""
    return [(i, e) for i, e in bound_tools(position_name, docs) if signed_in(e, d, now, run) is False]


def signin_task(d, missing, position, moved=False, lang=None):
    """(title, steps) of the founder's task to sign in to account-bound tools on one account, in the owner's
    language (of_locale) unless `lang` is given."""
    from of_locale import tr
    acct = account_of(d) or short(d)
    names = ", ".join(i.split("/", 1)[-1] for i, _ in missing)
    line = ("claude" if is_default(d) else f"CLAUDE_CONFIG_DIR={short(d)} claude")
    kw = dict(position=position, names=names, acct=acct, dir=short(d), line=line)
    why = (tr("of_capacity moved {position} there because no other account had room; until the sign-in exists, these "
              "tools are missing in the session.", lang, **kw) if moved else
           tr("Until it exists, of_capacity does not move {position} there.", lang, **kw))
    return (tr("Sign in to {names} in the account {acct}", lang, **kw),
            tr("The position {position} needs {names}. Their sign-in is kept per Claude account, and the account "
               "{acct} ({dir}) has none. {why}\n"
               "1. In a plain terminal tab, not with \"!\" inside an agent's session: {line}\n"
               "2. Type /mcp, choose {names} and sign in in the browser; then /exit.\n"
               "3. Say done: of_capacity checks that `claude mcp list` shows them Connected.", lang, why=why, **kw))


SIGNIN_TASK_EVERY_S = 24 * 3600


def file_signin_task(d, missing, position, ask=None, of_bin=None, moved=False, now=None, log=print):
    """File the founder's task for `missing` sign-ins on account `d`, once per account and tool a day (the task
    stays open until checked; the pulse reminds him). `ask(text, task=..)` is the balancer's; without it, `of ask`.
    Returns the tools it filed for."""
    now = now or time.time()
    cache = load_cache()
    done = (cache.get("_signin_tasks") or {}).get(d) or {}
    new = [(i, e) for i, e in missing if now - done.get(i, 0) >= SIGNIN_TASK_EVERY_S]
    if not new:
        return []
    title, steps = signin_task(d, missing, position, moved=moved)
    try:
        if ask:
            ask(title, task=steps)
        else:
            subprocess.run([sys.executable, of_bin or os.path.join(os.path.dirname(os.path.realpath(__file__)), "of"),
                            "ask", title, "--task", steps, "--from", actor()], capture_output=True, text=True, timeout=60,
                           check=True)
    except Exception as e:  # noqa: BLE001 - the move's own message still names what is missing
        log(f"  sign-in task for {short(d)} not filed: {type(e).__name__}: {e}")
        return []
    cache = load_cache()
    cache.setdefault("_signin_tasks", {}).setdefault(d, {}).update({i: now for i, _ in new})
    save_cache(cache)
    log(f"  founder's task: sign in to {', '.join(i for i, _ in new)} on {short(d)}")
    return [i for i, _ in new]


def add_bound(needs, seen, positions, dirs, docs=None, now=None, run=None):
    """Add the position's account-bound tools to what each session needs ("bound:<org>/<id>"), and to each account
    what is signed in there, with "bound:known" where `claude mcp list` was read. In place; nothing without a
    catalogue."""
    docs = tool_docs() if docs is None else docs
    if not docs:
        return
    entries = {}
    for p in positions:
        sid = ((p.get("holder") or {}).get("session") or "").strip()
        if sid in needs:
            b = bound_tools(p.get("name"), docs)
            needs[sid] = set(needs[sid]) | {"bound:" + i for i, _ in b}
            entries.update(b)
    for d in dirs:
        if not entries or mcp_servers(d, now, run) is None:
            continue
        have = {"bound:" + i for i, e in entries.items() if signed_in(e, d, now, run)} | {"bound:known"}
        seen[d] = (set(seen.get(d) or ()) | have) if seen.get(d) is not None else have


def tools_for(p, sid, target, log=print):
    """(argv, settings keys) of the position's tool profile for a session on `target` (of_tools.launch_parts:
    --mcp-config and allowedMcpServers / enabledPlugins), as of spawn passes them; ([], {}) without a catalogue."""
    if not p or not p.get("name"):
        return [], {}
    try:
        import of_tools
        return of_tools.launch_parts(p["name"], sid, target)
    except Exception as e:  # noqa: BLE001 - a position outside any catalogue, or a broken one (of tools check)
        if type(e).__name__ != "ToolsError":
            log(f"tools: no profile for {p['name']} ({type(e).__name__}: {e}); the account's own tools apply")
        return [], {}


TIER_SCALE = {"default_claude_ai": 1, "default_claude_max_5x": 5, "default_claude_max_20x": 20}  # Pro, Max 5x, Max 20x
RESUME_USD_PER_MTOK = 8.0  # a moved session's first turn writes its whole context to the new account's 1-hour cache
RESUME_SHARE_MAX = 0.10    # of the target's 5-hour window
RESUME_WEEK_PCT = 1.0      # or, with no 5-hour measure, percent of its week (about $20-30 on Max 20x, $1.2 on Pro)


def rate_tier(d):
    try:
        with open(account_file(d)) as fh:
            return (json.load(fh).get("oauthAccount") or {}).get("organizationRateLimitTier")
    except Exception:
        return None


def context_tokens(transcript):
    """The session's context at its last main-thread response: what a resume writes to the new account's cache."""
    try:
        size = os.path.getsize(transcript)
        with open(transcript, "rb") as fh:
            fh.seek(max(0, size - 3_000_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except Exception:
        return 0
    for ln in reversed(lines):
        if '"usage"' not in ln or '"assistant"' not in ln:
            continue
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        if j.get("isSidechain"):
            continue
        u = (j.get("message") or {}).get("usage") or {}
        return u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("input_tokens", 0)
    return 0


TIER_RANK = {"default_claude_max_20x": 3, "default_claude_max_5x": 2, "default_claude_ai": 1}
# $ per 1% of the week by plan, for ordering only, when an account's own week is not measured yet (2026-11-09:
# ~/.claude-d $25, ~/.claude-b $29, ~/.claude-c on Max 5x $19; Pro about 1/20 of an Ultra)
TIER_WEEK_USD = {"default_claude_max_20x": 26.0, "default_claude_max_5x": 19.0, "default_claude_ai": 1.3}


def blocked_until(r, now):
    """0 when the account accepts work by its last probe, else when it will again: the latest reset among the
    windows that stop it (inf when it is rejected and no window says until when). A window whose reset has passed
    stops nothing."""
    if not r.get("logged_in"):
        return float("inf")
    until = 0.0
    for w in (r.get("windows") or {}).values():
        if not isinstance(w, dict):
            continue
        u = w.get("utilization")
        if w.get("status") == "rejected" or (u is not None and u >= 100):
            until = max(until, _ts(w.get("resets_at") or "") or float("inf"))
    if r.get("status") == "rejected" and not until:
        until = float("inf")
    return until if until > now else 0.0


def rescue_plan(rows, sessions, positions, stopped, policy, now, skip=(), wcalib=None, tools=None):
    """Pure. The invariant (founder, 2026-11-10): no session stands on a usage limit while another account accepts
    work. `stopped`: {session: epoch of its limit stop}, read from its transcript (limit_stop), an observation. A
    stopped holder between turns whose account does not accept work goes to one that does. Predicted room, burn
    rates, calibrations and resume costs never veto this; they size relief and fill, which are optional. An account
    with a session stopped after its last probe is taken as stopped (the probe lags up to 30 minutes).
    Targets: accounts that accept work and are not about to stop (5-hour under relief_at, week under week_max), else
    any that accepts; the most capacity left first: observed free percent times the account's measured $ per 1%
    of its week (else its plan's, TIER_WEEK_USD), so a fresh Pro does not outrank an Ultra with a third of its week
    left; each move counts rescue_step points against its target, so a batch spreads. Production (`hold`) first, then priority tiers, then the oldest
    stop. A holder between turns on an account the probe saw refuse work for rescue_ahead_min (60) or longer yet
    goes too, after every stopped one: it would stop on its next turn. tools: ({session: tools_used}, {dir: what
    it has}); a target with every plugin the session used comes first, else any (its note says what is missing: a
    stopped session without a plugin can still work). `skip`: sessions whose move failed lately.
    Returns moves, at most rescue_moves; the rest go next run."""
    pos_by_session = {((p.get("holder") or {}).get("session") or "").strip(): p for p in positions}
    acct = {r["config_dir"]: r for r in rows}
    step = policy.get("rescue_step", 2)

    def cur(r, w):  # utilization now: a window whose reset has passed is empty
        x = (r.get("windows") or {}).get(w) or {}
        return (x.get("utilization") or 0) if (_ts(x.get("resets_at") or "") or float("inf")) > now else 0

    def blocked(d):
        r = acct.get(d)
        if r is None or blocked_until(r, now):
            return True
        seen = _ts(r.get("observed_at") or "") or 0
        return any(stopped.get(s["session"], 0) > seen for s in sessions.get(d, []))

    targets = [r for d, r in acct.items() if not blocked(d)]
    if not targets:
        return []
    taken = {r["config_dir"]: 0 for r in targets}

    needs, seen = tools or ({}, {})

    def key(r, sid=None):
        ok = cur(r, "five_hour") < policy["relief_at"] and cur(r, "seven_day") < policy["week_max"]
        miss = any(x.startswith(VETO) for x in lacking(needs.get(sid) or (), seen.get(r["config_dir"])))
        room = 100 - max(cur(r, "five_hour"), cur(r, "seven_day")) - step * taken[r["config_dir"]]
        usd = (wcalib or {}).get(r["config_dir"]) or TIER_WEEK_USD.get(r.get("tier"), 1.0)
        return (miss, not ok, -room * usd, -TIER_RANK.get(r.get("tier"), 0), r["config_dir"])
    ahead = policy.get("rescue_ahead_min", 60) * 60
    cands = []
    for d in acct:
        if not blocked(d):
            continue
        # the probe saw this account refuse work for rescue_ahead_min or longer yet: a holder between turns there
        # stops on its next turn (2026-11-11: storefront-hosts_keeper sat on spent ~/.claude-d with a check due at 06:48Z), so it
        # goes too, after the stopped ones. Within the hour a window reopens before most idle holders get work, and
        # each move costs a resume
        until = blocked_until(acct[d], now)
        # a probe with no reading (logged_in None: a timeout, a dropped link) is no evidence the account refuses work:
        # only its stopped sessions go (2026-11-11: the founder's Mac on a mobile hotspot, of_platform's check)
        doomed = bool(until) and until - now >= ahead and acct[d].get("logged_in") is not None
        for s in sessions.get(d, []):
            p = pos_by_session.get(s["session"])
            is_stopped = s["session"] in stopped
            if not (is_stopped or doomed) or not p or s.get("status") not in ("idle", "shell", "waiting") \
                    or s["session"] in skip:
                continue
            cands.append((not is_stopped, p["name"] not in policy["hold"], priority_tier(p, policy),
                          stopped.get(s["session"], until), p["name"], s["session"], d))
    moves = []
    for later, _, _, since, name, sid, d in sorted(cands)[:policy.get("rescue_moves", 6)]:
        t = min(targets, key=lambda r: key(r, sid))
        taken[t["config_dir"]] += 1
        why = (f"its account does not accept work until {_iso(since) if since != float('inf') else 'an unknown time'}"
               if later else f"stopped on its account's usage limit since {_iso(since)}")
        moves.append({"position": name, "session": sid, "from": d, "to": t["config_dir"], "rate": 0.0,
                      "reason": f"rescue: {why}; {short(t['config_dir'])} accepts work"})
    return moves


def stall_report(rows, sessions, positions, stopped, moves, now):
    """After a run: the holders still stopped on a limit on an account that does not accept work, while another
    account does, with why each is still there. Empty when the invariant holds (or no account accepts work)."""
    acct = {r["config_dir"]: r for r in rows}
    pos_by_session = {((p.get("holder") or {}).get("session") or "").strip(): p for p in positions}
    moved = {m["session"]: m for m in moves}

    def blocked(d):
        r = acct.get(d)
        seen = _ts((r or {}).get("observed_at") or "") or 0
        return r is None or bool(blocked_until(r, now)) or \
            any(stopped.get(s["session"], 0) > seen for s in sessions.get(d, []))
    accepting = [d for d in acct if not blocked(d)]
    if not accepting:
        return []
    out = []
    for d, xs in sessions.items():
        if not blocked(d):
            continue
        for s in xs:
            if s["session"] not in stopped:
                continue
            m = moved.get(s["session"])
            if m and m.get("done", True):
                continue
            p = pos_by_session.get(s["session"])
            why = (f"move failed: {m.get('error')}" if m else "no position in any registry" if not p
                   else f"session status {s.get('status')}" if s.get("status") not in ("idle", "shell", "waiting")
                   else "a recent move of it failed, or over this run's rescue_moves")
            out.append({"session": s["session"], "position": p["name"] if p else None, "account": d,
                        "since": _iso(stopped[s["session"]]), "why": why})
    return out


def accepting_accounts(rows, sessions, stopped, now):
    """Accounts that accept work now: no window of the last probe stops them, and no session on them stopped
    after that probe."""
    out = []
    for r in rows:
        seen = _ts(r.get("observed_at") or "") or 0
        if not blocked_until(r, now) and not any(stopped.get(s["session"], 0) > seen
                                                  for s in sessions.get(r["config_dir"], [])):
            out.append(r["config_dir"])
    return out


def pool_numbers(rows, sessions, rates, wcalib, now):
    """The capacity question's numbers, one line per account: week and 5-hour use, when it reopens if it is stopped,
    its sessions and their spend now."""
    lines = []
    for r in rows:
        if not r.get("logged_in"):
            continue
        w = r.get("windows") or {}
        f, wk = (w.get("five_hour") or {}).get("utilization"), (w.get("seven_day") or {}).get("utilization")
        until = blocked_until(r, now)
        xs = sessions.get(r["config_dir"], [])
        load = sum(rates.get(s["session"], 0.0) for s in xs)
        lines.append(f"- {short(r['config_dir'])} ({r.get('account') or '?'}, {r.get('tier') or 'plan unknown'}): week "
                     f"{wk if wk is not None else '?'}%" + (f", 5 hours {f}%" if f is not None else "")
                     + (f", stopped until {_iso(until)}" if until and until != float('inf') else
                        ", stopped (no reset given)" if until else ", accepts work")
                     + f"; {len(xs)} sessions, ${load:.0f}/h now"
                     + (f"; ${wcalib[r['config_dir']]:.0f} per 1% of its week" if (wcalib or {}).get(r["config_dir"]) else ""))
    return "\n".join(lines)


def invariant_step(state, stalls, accepting, rescued, spent, now, numbers="", window_s=5 * 3600):
    """Pure bookkeeping of the invariant across runs (of_architect's review of the rescue, 2026-11-11). The founder must
    never be the monitor again.
    state: {"stalled": [session ids], "runs": {session: runs stalled}, "rescues": {session: [epochs]},
    "told_pool": epoch}; stalls: stall_report(); accepting: accepting_accounts(); rescued: [(session, position)]
    moved by rescue this run; spent: holders stopped on a limit while no account accepts work.
    Returns (state, actions):
    - ("event", payload) when the stalled set changes, the empty set too (a stall's duration is measurable), with the
      accounts that accepted work then;
    - ("self", text) for a stall that survived two runs (mid-turn, a failed move, over the cap): work for of_capacity;
    - ("pool", text) at most once per 5-hour window when the pool is short (a session rescued twice within one
      5-hour window: the pool is running out, not a move gone wrong) or spent (no account accepts work): work for
      of_capacity, which files the founder's capacity question itself with the numbers (a money decision reaches him
      only as its owner's ask, of_conductor, 2026-11-11, msg 01a1c956). Rescues go on either way."""
    st = {"stalled": list(state.get("stalled") or []), "runs": dict(state.get("runs") or {}),
          "rescues": {k: [x for x in v if now - x < window_s] for k, v in (state.get("rescues") or {}).items()},
          "told_pool": state.get("told_pool") or 0}
    st["rescues"] = {k: v for k, v in st["rescues"].items() if v}
    actions = []
    ids = sorted(s["session"] for s in stalls)
    if ids != sorted(st["stalled"]):
        actions.append(("event", {"stalled": stalls, "accepting": sorted(accepting)}))
    st["stalled"] = ids
    runs = {sid: st["runs"].get(sid, 0) + 1 for sid in ids}
    for s in stalls:
        if runs[s["session"]] == 2:
            actions.append(("self", f"Invariant broken for two balancer runs: {s['position'] or s['session']} stopped on "
                                    f"a usage limit on {short(s['account'])} since {s['since']} ({s['why']}), while "
                                    f"{', '.join(short(a) for a in sorted(accepting))} accept work. Find why and "
                                    "move it, or fix what stops the rescue (docs/capacity.md, \"The invariant\")."))
    st["runs"] = runs
    short_ = []
    for sid, name in rescued:
        if st["rescues"].get(sid):
            short_.append(name or sid)
        st["rescues"].setdefault(sid, []).append(now)
    if (short_ or spent) and now - st["told_pool"] >= window_s:
        what = (f"no account accepts work, and {len(spent)} session(s) stand on a limit" if spent else
                f"{', '.join(short_)} had to be rescued twice within one 5-hour window, so the accounts it moves to "
                "fill as fast as it moves")
        actions.append(("pool",
                        "Capacity question for the founder (of_capacity's balancer, once per 5-hour window): the pool "
                        f"of Claude accounts is {'spent' if spent else 'short'}: {what}. Rescues go on by themselves; "
                        "his call is money: wait for the accounts to reopen, or add capacity. Accounts now:\n"
                        + numbers + "\nOne more Max 20x account holds about $2,600 of list-price work a week "
                        "(measured $25-29 per 1% of a week); its price is his to check. Unless an ask of yours on it "
                        "is open, file it yourself: of ask in the AGENTS.md form, --tags money and a --due."))
        st["told_pool"] = now
    return st, actions


def decide(rows, sessions, positions, rates, calib, policy, now, stopped=None, skip=(), failed=(), connectors=None,
           contexts=None, wcalib=None):
    """Pure. Rescue first (observed stops, never vetoed by a prediction), then relief and fill (balance_plan) for
    the rest."""
    rescue = rescue_plan(rows, sessions, positions, stopped or {}, policy, now, skip=failed, wcalib=wcalib,
                         tools=connectors)
    rest = balance_plan(rows, sessions, positions, rates, calib, policy, now,
                        skip=set(skip) | {m["session"] for m in rescue}, connectors=connectors, contexts=contexts,
                        wcalib=wcalib)
    return rescue + rest


def balance_plan(rows, sessions, positions, rates, calib, policy, now, skip=(), connectors=None, contexts=None,
                 wcalib=None):
    """Pure. rows: status rows; sessions: {dir: live sessions}; rates: {session: $/h}; calib: {dir: $ per 1%
    of its 5-hour window}; connectors: ({session: claude.ai connectors it uses}, {dir: connectors seen there}).
    Fill never moves a session onto an account that lacks a connector or plugin it uses; relief does for a
    connector (the move's note says what is missing), never for a plugin (2026-11-11: aws_keeper lost Cloudflare). Returns [{position, session, from, to, rate,
    reason}], at most policy max_moves."""
    needs, seen = connectors or ({}, {})

    def prior_calib(r, cal=None):
        """An account not yet calibrated: the others' $ per 1% scaled by plan tier (Pro is 1/20 of Max 20x)."""
        cal = calib if cal is None else cal
        mine = TIER_SCALE.get(r.get("tier"))
        known = sorted(cal[x["config_dir"]] / TIER_SCALE[x.get("tier")] for x in rows
                       if cal.get(x["config_dir"]) and TIER_SCALE.get(x.get("tier")))
        return known[len(known) // 2] * mine if known and mine else None

    def lacks(sid, d):  # fill: a missing connector or plugin
        return bool(lacking(needs.get(sid) or (), seen.get(d)))

    def lacks_plugin(sid, d):  # relief: a missing plugin or account-bound tool (connectors as before: the note says)
        return any(x.startswith(VETO) for x in lacking(needs.get(sid) or (), seen.get(d)))
    pos_by_session = {((p.get("holder") or {}).get("session") or "").strip(): p for p in positions}
    acct = {r["config_dir"]: r for r in rows if r.get("logged_in")}

    def win(r, w):
        x = (r.get("windows") or {}).get(w) or {}
        return x.get("utilization"), _ts(x.get("resets_at") or "")

    def stalled(r):
        f, _ = win(r, "five_hour")
        wk, _ = win(r, "seven_day")
        return (f is not None and f >= policy["relief_at"]) or (wk is not None and wk >= policy["week_max"]) \
            or r.get("status") == "rejected" or week_runs_out(r)

    def week_runs_out(r):
        """Its week reaches week_max within relief_ahead_h at the current load, before it resets: relieve it now,
        while its sessions still run (2026-11-08: ~/.claude-e at 95% with 39 sessions, a fresh ~/.claude-d empty)."""
        wk, wk_reset = win(r, "seven_day")
        wcal = (wcalib or {}).get(r["config_dir"])
        load = sum(rates.get(s["session"], 0.0) for s in sessions.get(r["config_dir"], []))
        if wk is None or not wk_reset or not wcal or load <= 0:
            return False
        hours_to_cap = (policy["week_max"] - wk) * wcal / load
        return hours_to_cap < min(policy.get("relief_ahead_h", 6), (wk_reset - now) / 3600)

    def movable(d, relief=False):
        # On an account about to stall, a session with background shells or one already stopped on the limit
        # dialog stands anyway: relief takes it (its shells stop, listed in its note). 2026-11-07: ten Acme
        # sessions stood on ~/.claude at 100% because relief took idle sessions only. Fill takes idle ones only.
        # Positions in `hold` (production) move only by relief, off an account exhausted or about to be, and they go
        # first there: a stalled keeper costs the most (founder, ask 44a3b1f9, 2026-11-08). Fill never takes them.
        ok = ("idle", "shell", "waiting") if relief else ("idle",)
        out = []
        for s in sessions.get(d, []):
            p = pos_by_session.get(s["session"])
            held = bool(p) and p["name"] in policy["hold"]
            if not p or (held and not relief) or s.get("status") not in ok or s["session"] in skip:
                continue
            out.append((rates.get(s["session"], 0.0), p["name"], s["session"]))
        return sorted(out, key=lambda x: (x[1] not in policy["hold"], tier(x[1]), -x[0], x[1]))

    def tier(name):
        return priority_tier(next((p for p in positions if p["name"] == name), {}), policy)

    def room_of(sink, extra_load=0.0, horizon_h=None):
        """$/h the account can still take: under fill_to of its 5-hour window and within its remaining week, sized
        by its own calibration or, without one, by plan tier from the others'. Also the $ a resume may cost there.
        An account that reports no 5-hour window but has a measured week is sized by its week alone: on 2026-11-10
        three Max 20x accounts had shown no 5-hour window in 225 probes each while carrying $100-115/h, and the
        tier prior scaled from the Max 5x one ($376 a window) said they had no room, so two sessions stood on the
        5x account's limit while the others had a third of their week left. A week just reset, with no measure of
        its own yet, is measured by the others' week scaled by tier; it still needs its own week reading (use and
        reset), which caps the room."""
        d = sink["config_dir"]
        f, f_reset = win(sink, "five_hour")
        load = sum(rates.get(s["session"], 0.0) for s in sessions.get(d, [])) + extra_load
        cal = calib.get(d) or prior_calib(sink)
        # a week just reset has no measure of its own yet: the others' week, scaled by tier, sizes it (2026-11-12:
        # ~/.claude-g reset to 0% looked to have no room, and fill and relief left it empty). Only with the account's
        # own week reading, use and reset, which caps the room below; without one, unknown is not infinite
        # (of_review, PR 55: a target at 90% of its week with no reset took $408/h)
        wk0, wk0_reset = win(sink, "seven_day")
        week_only = f is None and wk0 is not None and bool(wk0_reset) \
            and bool((wcalib or {}).get(d) or prior_calib(sink, wcalib or {}))
        if f is None or not f_reset:  # no 5-hour window open yet: a whole window ahead, nothing used
            f, f_reset = 0, now + 5 * 3600
        if week_only:
            room = float("inf")
        elif cal:
            hours = max(0.25, (f_reset - now) / 3600)
            room = (policy["fill_to"] - (f + load * hours / cal)) * cal / hours
        else:
            room = 0.0  # unknown capacity is not infinite (2026-11-07: a new Pro account)
        wk, wk_reset = win(sink, "seven_day")
        wcal = (wcalib or {}).get(d) or prior_calib(sink, wcalib or {})
        if wk is not None and wk_reset and wcal:  # what it takes must last until its week resets (or the horizon)
            hours_w = max(1.0, (wk_reset - now) / 3600)
            if horizon_h:
                hours_w = min(hours_w, horizon_h)
            room = min(room, ((policy["week_max"] - 3 - wk) * wcal - load * hours_w) / hours_w)
        # with no 5-hour measure (Max accounts report none), a resume may cost a share of the week instead
        resume_max = cal * 100 * RESUME_SHARE_MAX if cal else (wcal or 0) * RESUME_WEEK_PCT
        return room, resume_max

    def resume_ok(sid, resume_max):
        return (contexts or {}).get(sid, 0) * RESUME_USD_PER_MTOK / 1e6 <= resume_max

    def relief_horizon(r):
        """Hours a relief target must carry what it takes: a day, or only until the source's 5-hour window reopens
        when that window alone stops it (the sessions go back to work there; fill may bring them back)."""
        h = policy.get("relief_horizon_h", 24)
        f, f_reset = win(r, "five_hour")
        wk, _ = win(r, "seven_day")
        if f is not None and f >= policy["relief_at"] and f_reset and (wk is None or wk < policy["week_max"]) \
                and not week_runs_out(r):
            h = min(h, max(1.0, (f_reset - now) / 3600))
        return h

    def waits_out(r):
        """Stalled only by a 5-hour window that reopens soon, with the week fine: waiting beats moving. 2026-11-07:
        relief moved 14 sessions off ~/.claude-e at 97% of a window reopening in under an hour, onto a small Pro account
        that overflowed at once and held them for two hours more."""
        f, f_reset = win(r, "five_hour")
        wk, _ = win(r, "seven_day")
        return (r.get("status") != "rejected" and f is not None and f >= policy["relief_at"] and f_reset
                and f_reset - now <= policy.get("relief_wait_min", 90) * 60
                and (wk is None or wk < policy["week_max"]) and not week_runs_out(r))

    moves = []
    # 1. relief: an account about to stall sheds sessions to accounts that can take them (room, resume cost), unless
    #    the window it hit reopens soon
    for d, r in acct.items():
        if not stalled(r) or waits_out(r):
            continue
        # a target with no reading at all is no target (headroom None; of_review, PR 55: it raised a TypeError here)
        targets = sorted((x for x in acct.values()
                          if not stalled(x) and x["config_dir"] != d and headroom(x) is not None),
                         key=lambda x: -headroom(x))
        added = {x["config_dir"]: 0.0 for x in targets}
        for rate, name, sid in movable(d, relief=True):
            if len(moves) >= policy.get("relief_moves", policy["max_moves"]):
                break
            for t in targets:  # relief: the target must last a day, not its whole week (the source stalls otherwise)
                room, resume_max = room_of(t, added[t["config_dir"]], horizon_h=relief_horizon(r))
                if rate <= room and resume_ok(sid, resume_max) and not lacks_plugin(sid, t["config_dir"]):
                    moves.append({"position": name, "session": sid, "from": d, "to": t["config_dir"], "rate": rate,
                                  "reason": "relief: the account is about to stall"})
                    added[t["config_dir"]] += rate
                    break
    # 2. fill: the account whose week resets first takes load until its 5-hour window is projected full; one with no
    #    room left, or none that fits what can move, passes the turn to the next, which still resets before its
    #    sources (2026-11-12: ~/.claude-a, resetting 3 hours before a freshly reset
    #    ~/.claude-g, had no room, and fill moved nothing while ~/.claude ran toward its weekly limit)
    sinks = sorted((r for r in acct.values() if not stalled(r) and win(r, "seven_day")[1]),
                   key=lambda r: win(r, "seven_day")[1])
    for sink in sinks:
        if len(moves) >= policy["max_moves"]:
            break
        d = sink["config_dir"]
        room, resume_max = room_of(sink, sum(m["rate"] for m in moves if m["to"] == d))
        if room < policy["min_rate"]:
            continue
        week_reset = win(sink, "seven_day")[1]
        sources = [x for x in acct.values() if x["config_dir"] != d and (win(x, "seven_day")[1] or 0) > week_reset]
        cands = sorted((c + (x["config_dir"],) for x in sources for c in movable(x["config_dir"])),
                       key=lambda c: (tier(c[1]), -c[0], c[1]))
        planned = {m["session"] for m in moves}  # relief took it already: one move per session and run (2026-11-11)
        before = len(moves)
        for rate, name, sid, src in cands:
            if sid in planned or len(moves) >= policy["max_moves"] or rate < policy["min_rate"] or rate > room \
                    or lacks(sid, d):
                continue
            if not resume_ok(sid, resume_max):
                continue  # its first turn would eat too much of the sink's 5-hour window
            moves.append({"position": name, "session": sid, "from": src, "to": d, "rate": rate,
                          "reason": f"fill: its week resets first; 5-hour window projected under {policy['fill_to']}%"})
            room -= rate
        if len(moves) > before:
            break  # one sink per run
    return moves


def balance(positions, dry_run=False, log=print, policy=None, now=None, ask=None):
    """One balancing run: read capacity (probing at most every 30 min), measure each session's spend over the
    last 30 minutes, plan, and move (between turns, in the same herdr pane). Returns the plan."""
    policy = policy or load_policy()
    now = now or time.time()
    plan = load_reboot_plan()
    if plan and not dry_run:  # a restart is prepared: a move now would resume from a stale transcript copy
        log(f"balance: paused, a restart of the Mac was prepared at {plan.get('prepared_at')} "
            "(`of capacity reboot restore` ends it)")
        return []
    import fcntl
    lockf = os.path.join(STATE_DIR, "capacity", "balance.lock")
    os.makedirs(os.path.dirname(lockf), mode=0o700, exist_ok=True)
    lock = open(lockf, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("balance: another run is in progress")
        return []
    rows = status(positions, now=now)
    dirs = [r["config_dir"] for r in rows if r.get("logged_in")]
    try:  # agent sessions stay in bypass (founder, 2026-11-08)
        heal_permission_modes(log=log, dirs=dirs, dry_run=dry_run)
    except Exception as e:
        log(f"balance: permission modes not checked: {e}")
    sessions = {d: live_sessions(d) for d in dirs}
    try:
        prune_move_backups({x.get("name") for xs in sessions.values() for x in xs if x.get("name")}, now=now,
                           log=log, dry_run=dry_run)
    except Exception as e:
        log(f"balance: move backups not pruned: {e}")
    index = current_index(dirs, now)
    rates = burn_rates(dirs, now, index)
    calib = {r["config_dir"]: calibrate(r, now, index) for r in rows if r.get("logged_in")}
    cache0 = load_cache()
    wcalib = {r["config_dir"]: calibrate_week(r, now, index, week_history(cache0, r["config_dir"], now))
              for r in rows if r.get("logged_in")}
    cache = load_cache()
    recent = {sid: t for sid, t in (cache.get("_balanced") or {}).items() if now - t[0] < t[1]}
    tiers = {os.path.expanduser(k): v for k, v in (policy.get("tiers") or {}).items()}
    for r in rows:  # capacity.yaml `tiers:` overrides a profile not yet refreshed after a plan change
        r.setdefault("tier", tiers.get(r["config_dir"]) or rate_tier(r["config_dir"]))
    needs = {x["session"]: tools_used(find_transcript(d, x["session"]) or "")
             for d, xs in sessions.items() for x in xs if x.get("status") in ("idle", "shell", "waiting")}
    contexts = {x["session"]: context_tokens(find_transcript(d, x["session"]) or "")
                for d, xs in sessions.items() for x in xs if x.get("status") == "idle"}
    # connectors from claude mcp list (None: unknown, no veto); plugins from the config dir's files, less those whose
    # server the same list shows signed out
    seen = {d: account_tools(connectors_seen(d), plugins_ready(d)) for d in dirs}
    add_bound(needs, seen, positions, dirs)
    stopped = {}
    for d, xs in sessions.items():
        for x in xs:
            if x.get("status") in ("idle", "shell", "waiting"):
                t = limit_stop(find_transcript(d, x["session"]) or "")
                if t:
                    stopped[x["session"]] = t
    failed = {sid for sid, t in recent.items() if len(t) > 2 and t[2] == "fail"}
    plan_ = decide(rows, sessions, positions, rates, calib, policy, now, stopped=stopped, skip=set(recent),
                   failed=failed, connectors=(needs, seen), contexts=contexts, wcalib=wcalib)
    untrusted = {}
    offline = bool(plan_) and not dry_run and not api_online()
    if offline:
        log("balance: the API does not answer a connect (of_net.online); no moves this run, the plan waits")
    for m in plan_:
        log(f"balance: {m['position']} {short(m['from'])} -> {short(m['to'])} (${m['rate']:.0f}/h; {m['reason']})")
        if dry_run or offline:
            continue
        p = next((x for x in positions if x["name"] == m["position"]), None)
        note = ("of_capacity's balancer moved you to use model capacity that would otherwise expire unused "
                "(founder, 2026-11-06). Nothing else changed: same session, pane and name. Carry on.")
        try:  # only idle holders are planned (no background shells); their scheduled tasks are in the note
            r = move(p, m["to"], allow_shells=True, log=lambda s: None, extra_note=note,
                     of_bin=os.path.join(os.path.dirname(os.path.realpath(__file__)), "of"),
                     allow_unsigned=str(m.get("reason") or "").startswith("rescue"), ask=ask)
            m["done"] = True
            recent[m["session"]] = (now, policy.get("cooldown_h", 2) * 3600)  # no ping-pong
            of_events.emit("capacity_balanced", dict(m, pane=r.get("pane")), position=m["position"],
                           session=m["session"])
        except MoveError as e:
            m["done"], m["error"], m["stuck"] = False, str(e), bool(e.stuck)
            recent[m["session"]] = (now, failed_hold_s(e), "fail")
            of_events.emit("capacity_balanced", dict(m), position=m["position"], session=m["session"])
            log(f"balance: {m['position']} not moved: {e}")
            if e.untrusted:
                untrusted.setdefault(e.untrusted, []).append(m["position"])
            if e.stuck:  # a pane left in a dialog loses every message typed into it (aws_keeper, 2026-11-07)
                if ask:
                    ask(None, task=stuck_task(m["position"], e), due="+30m")
                log("balance: stopped this run; a pane is stuck")
                break
    stalls = stall_report(rows, sessions, positions, stopped, plan_ if not dry_run else [], now)
    accepting = accepting_accounts(rows, sessions, stopped, now)
    if stalls:  # the invariant is checked, not assumed (2026-11-10: the founder was the one who noticed)
        log(f"balance: {len(stalls)} session(s) still stopped on a usage limit while another account accepts work: "
            + "; ".join(f"{s['position'] or s['session'][:8]} ({s['why']})" for s in stalls))
    if not dry_run:
        where = {x["session"]: d for d, xs in sessions.items() for x in xs}
        spent = [sid for sid in stopped if where.get(sid) not in accepting] if not accepting else []
        rescued = [(m["session"], m["position"]) for m in plan_ if m.get("done") and m["reason"].startswith("rescue")]
        cache = load_cache()
        state, actions = invariant_step(cache.get("_invariant") or {"stalled": cache.get("_stalled") or []}, stalls,
                                        accepting, rescued, spent, now,
                                        numbers=pool_numbers(rows, sessions, rates, wcalib, now))
        cache["_invariant"] = state
        save_cache(cache)
        of_bin = os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
        carded = set()
        for kind, x in actions:
            if kind == "event":
                of_events.emit("capacity_stall", x)
                continue
            to = capacity_position(policy)  # "self" and "pool": work for the capacity position's session
            if not to:
                if kind == "self" and kind in carded:
                    continue  # one card per run names every stalled session
                carded.add(kind)
                text, options = owner_card(kind, stalls, accepting, spent,
                                           pool_numbers(rows, sessions, rates, wcalib, now))
                if ask and not dry_run:
                    ask(text, options) if options else ask(text)
                log(f"balance: invariant {kind}: no capacity position; {'the owner has a card' if ask else text}")
                continue
            r = subprocess.run([sys.executable, of_bin, "send", to, "--from", to, x], capture_output=True, text=True,
                               timeout=60, env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
            log(f"balance: invariant {kind}: {(r.stdout or r.stderr).strip()[-160:]}")
    if plan_ and not dry_run:
        cache = load_cache()
        cache["_balanced"] = recent
        asked = set(cache.get("_trust_asked") or [])
        new_folders = {f: ps for f, ps in untrusted.items() if f not in asked}
        if new_folders and ask:
            from of_locale import tr
            ask(tr("Trust these folders in another Claude account, so the balancer can move their positions? No "
                   "account marks them trusted, so no move starts: the session would stop on the folder-trust dialog, "
                   "and the tool may not press \"Yes, I trust\". Folders and positions: {folders}. Without a decision "
                   "these positions stay on their current account and stop when its limit runs out.",
                   folders="; ".join(f"{f} ({', '.join(ps)})" for f, ps in new_folders.items())),
                tr("trust them in the target account") + "|" + tr("leave the positions where they are"))
            cache["_trust_asked"] = sorted(asked | set(new_folders))
        save_cache(cache)
    return plan_


# ------------------------------------------------------- listener processor
#
# RFC 0001 §5.1a: the balancer's spend comes from the store by cursor, not from rereading transcripts.
# The listener ticks it every 5 minutes: new transcript_record rows (id > cursor) with a message_id go
# into view_capacity_spend, one row per API response, kept 6 hours. Until the store's backfill gives
# every usage row of the last 6 hours a message_id, the view is incomplete and the balancer keeps the
# transcript cursor; once it is complete, the view is rebuilt once and becomes the source. The balance
# itself (moves take minutes) runs as a detached `of capacity balance`, one at a time (a lock).

SPEND_DDL = """
CREATE TABLE IF NOT EXISTS view_capacity_spend (message_id TEXT PRIMARY KEY, session_id TEXT, epoch REAL,
  cost REAL, config_dir TEXT);
CREATE INDEX IF NOT EXISTS view_capacity_spend_epoch ON view_capacity_spend (epoch);
"""


def _meta(conn, key, value=None):
    if value is None:
        r = conn.execute("SELECT value FROM listener_meta WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None
    conn.execute("INSERT OR REPLACE INTO listener_meta VALUES (?, ?)", (key, str(value)))


def row_cost(inp, out, cread, cw, cw5, cw1):
    if cw5 is None and cw1 is None:
        cw1, cw5 = cw or 0, 0
    return ((inp or 0) * PRICES["in"] + (out or 0) * PRICES["out"] + (cw1 or 0) * PRICES["cw1h"]
            + (cw5 or 0) * PRICES["cw5"] + (cread or 0) * PRICES["cr"]) / 1e6


def store_complete(conn, now, keep_s=SPEND_KEEP_S):
    """Every usage row of the last `keep_s` seconds carries a message_id (the backfill is done)."""
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - keep_s))
    r = conn.execute("SELECT count(*) FROM transcript_record WHERE timestamp >= ? AND input_tokens IS NOT NULL "
                     "AND message_id IS NULL", (since,)).fetchone()
    return r[0] == 0


def _owns(conn, mid, sid):
    """Whether session `sid` may write message `mid` into the view. A fork (a side question asked in a
    session) copies the session's records under its own id, and its Stop can come first while the session is
    in a long turn: the response belongs to the session whose earliest response in the view is older."""
    row = conn.execute("SELECT session_id FROM view_capacity_spend WHERE message_id = ?", (mid,)).fetchone()
    if not row or row[0] == sid:
        return True
    first = lambda s: conn.execute("SELECT min(epoch) FROM view_capacity_spend WHERE session_id = ?",
                                   (s,)).fetchone()[0]
    mine, theirs = first(sid), first(row[0])
    return mine is not None and theirs is not None and mine < theirs


def ingest_spend(conn, now, keep_s=SPEND_KEEP_S, batch=5000):
    """Advance the cursor over transcript_record into view_capacity_spend. Returns rows read."""
    conn.executescript(SPEND_DDL)
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - keep_s))
    if _meta(conn, "capacity_view_complete") != "1" and store_complete(conn, now, keep_s):
        conn.execute("DELETE FROM view_capacity_spend")  # rebuild once, now that old rows have message ids
        first = conn.execute("SELECT min(id) FROM transcript_record WHERE timestamp >= ?", (since,)).fetchone()[0]
        _meta(conn, "capacity_tr_cursor", (first or 1) - 1)
        _meta(conn, "capacity_view_complete", 1)
    cur = _meta(conn, "capacity_tr_cursor")
    if cur is None:
        first = conn.execute("SELECT min(id) FROM transcript_record WHERE timestamp >= ?", (since,)).fetchone()[0]
        cur = (first or 1) - 1
    cur, n = int(cur), 0
    # transcript_own leaves out a fork's copies of its parent's records (of_platform, 2026-11-06: a fork is
    # marked in the transaction of its first rows, so a reader by id never sees a copy before its mark);
    # an older store without it falls back to transcript_record and the _owns rule
    own = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'transcript_own'").fetchone() is not None
    src = "transcript_own" if own else "transcript_record"
    while True:
        rows = conn.execute(
            "SELECT id, source_path, session_id, message_id, timestamp, input_tokens, output_tokens, "
            f"cache_read_tokens, cache_write_tokens, cache_write_5m_tokens, cache_write_1h_tokens "
            f"FROM {src} WHERE id > ? ORDER BY id LIMIT ?", (cur, batch)).fetchall()
        if not rows:
            break
        for rid, path, sid, mid, ts, inp, out, cr, cw, cw5, cw1 in rows:
            if mid and inp is not None:
                m = _CONFIG_DIR_RE.match(path or "")
                t = _ts(ts or "")
                if m and t and t >= now - keep_s:
                    if f"{os.sep}subagents{os.sep}" in path:
                        sid = path.split(os.sep)[-3]
                    if not own and not _owns(conn, mid, sid):
                        continue
                    conn.execute("INSERT OR REPLACE INTO view_capacity_spend VALUES (?,?,?,?,?)",
                                 (mid, sid, t, row_cost(inp, out, cr, cw, cw5, cw1), m.group(1)))
        cur, n = rows[-1][0], n + len(rows)
    _meta(conn, "capacity_tr_cursor", cur)
    conn.execute("DELETE FROM view_capacity_spend WHERE epoch < ?", (now - keep_s,))
    return n


def store_index(conn):
    """The view in spend_index()'s shape, or None while the store is not complete."""
    try:
        if _meta(conn, "capacity_view_complete") != "1":
            return None
        return {mid: [t, sid, cost, d] for mid, sid, t, cost, d in
                conn.execute("SELECT message_id, session_id, epoch, cost, config_dir FROM view_capacity_spend")}
    except Exception:
        return None


def spawn_balance():
    """A detached `of capacity balance` (moves take minutes; the listener must not wait)."""
    of_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
    logf = os.path.join(STATE_DIR, "capacity", "balance.log")
    os.makedirs(os.path.dirname(logf), mode=0o700, exist_ok=True)
    env = dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK="")
    with open(logf, "a") as fh:
        subprocess.Popen([sys.executable, of_path, "capacity", "balance"], stdout=fh, stderr=fh, env=env,
                         start_new_session=True, stdin=subprocess.DEVNULL)


TICK_LOG_MAX = 1 << 20


def _tick_lock():
    """The capacity tick's lock file, taken without waiting; None when another tick holds it."""
    import fcntl
    lockf = os.path.join(STATE_DIR, "capacity", "tick.lock")
    os.makedirs(os.path.dirname(lockf), mode=0o700, exist_ok=True)
    lock = open(lockf, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return None
    return lock


def run_tick(conn, now, log=print, spawn=None):
    """The capacity tick's work, in `of capacity tick` (spawn_tick), never in the listener's loop: the spend view, the
    loop costs, spend per goal, then a detached balance. One at a time (a lock); returns False when another runs.

    of_asks, 2026-11-13 (msg 01a1d67e-9e48): run inside the listener, this tick once held its loop for 442 s (and
    18 s, 46 s and 135 s around it) while the store was contended. No heartbeat was written meanwhile, so every
    reader took the views as stale and projected the whole log, and the founder's questions screen stuttered."""
    lock = _tick_lock()
    if lock is None:
        log("capacity tick: another tick is running")
        return False
    try:
        t0 = time.time()
        n = ingest_spend(conn, now)
        try:
            loop_costs(conn, now)  # RFC 0005 step 5: cost per cycle for of_studio's loop card
        except Exception as e:
            log(f"capacity tick: loop costs: {e}")
        try:  # spend per goal (bin/of_spend.py; docs/registry.md, Goals, "Spend per goal"): its own view and cursor
            import of_spend
            of_spend.tick(conn, now)
        except Exception as e:
            log(f"capacity tick: spend per goal: {e}")
        if load_policy().get("auto"):
            (spawn or spawn_balance)()
        if time.time() - t0 > 30:
            log(f"capacity tick: {time.time() - t0:.0f} s ({n} transcript records read)")
        return True
    finally:
        lock.close()


def spawn_tick():
    """A detached `of capacity tick`, unless one runs: the listener only starts it and goes on."""
    lock = _tick_lock()
    if lock is None:
        return False
    lock.close()
    of_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
    logf = os.path.join(STATE_DIR, "capacity", "tick.log")
    try:  # one older copy is kept; the log never grows past twice this (of_review on PR 116)
        if os.path.getsize(logf) > TICK_LOG_MAX:
            os.replace(logf, logf + ".1")
    except OSError:
        pass
    env = dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK="")
    with open(logf, "a") as fh:
        subprocess.Popen([sys.executable, of_path, "capacity", "tick"], stdout=fh, stderr=fh, env=env,
                         start_new_session=True, stdin=subprocess.DEVNULL)
    return True


class ListenerProcessor:
    """of_listen processor (duck-typed: name, version, tick_s, reset, handle, tick)."""
    name = "capacity"
    version = "2"  # 2: reads transcript_own, so a fork never takes its parent's responses (rebuilds the view)
    tick_s = 300

    def reset(self, conn):
        conn.executescript(SPEND_DDL)
        conn.execute("DELETE FROM view_capacity_spend")
        conn.execute("DELETE FROM listener_meta WHERE key IN ('capacity_tr_cursor', 'capacity_view_complete')")

    def handle(self, conn, rows):
        return False  # its input is transcript_record, read in tick by its own cursor

    def tick(self, conn, now):
        spawn_tick()  # the work runs in its own process (run_tick): the listener's loop never waits on it
        return False
