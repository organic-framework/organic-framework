"""OF's own health on this machine (docs/health.md; hand-over 6d9a8305 from of_architect).

`of health run` (launchd, every minute, at Standard priority, never on the listener's loop) measures the machine's
checks of docs/health.md §2, writes their state to health.json, records of.health_changed only when a check changes
state, and tells a check's owner once when it fails and once when it is back. `of health` prints the table.
`of smoke` is §4's smoke check: run by the repository's pre-commit gate on the content being committed, and by
`of health run` on the working copy's newest commit. of-server's own checks (`relay.oldest`) are of_server's.

The limits are docs/health.md §2's starting values; a check's owner tunes them there, with the evidence.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

import of_events

HERE = os.path.dirname(os.path.realpath(__file__))
LABEL = "dev.organic-framework.health"
WINDOW_S = 600          # hooks.errors and hooks.latency look at the last 10 minutes
TELL_AFTER_S = 300      # a check's owner hears of a failure that lasted 5 minutes, not of each flap around a limit
FYI_FROM = "organic-framework-build:of_platform"
TEAM_ORG = "organic-framework-build"

# check -> (degraded past, failed past, "above" or "below", owner); per-processor and per-view checks are made below
LIMITS = {
    "server.health": (2.0, 20.0, "above", "of_server"),
    "hooks.errors": (0.01, 0.10, "above", "of_platform"),
    "hooks.latency": (1.0, 5.0, "above", "of_platform"),
    "machine.memory": (0.80, 0.95, "above", "of_platform"),
    "machine.disk": (25.0, 20.0, "below", "of_platform"),
}
LISTENER_LAG = (120.0, 600.0)  # seconds, docs/health.md §2


def state_path():
    return os.path.join(of_events.STATE_DIR, "health.json")


def smoke_path():
    return os.path.join(of_events.STATE_DIR, "smoke.json")


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def grade(value, degraded, failed, direction="above"):
    """ok, degraded or failed for a value against its two limits (None: no reading, failed)."""
    if value is None:
        return "failed"
    if direction == "below":
        return "failed" if value < failed else "degraded" if value < degraded else "ok"
    return "failed" if value > failed else "degraded" if value > degraded else "ok"


# ------------------------------------------------------------------ the measurements

def server_health(url=None, timeout=20.0, opener=None):
    """Seconds of-server took to answer /v1/health, or None when it did not answer within `timeout`."""
    import urllib.request
    try:
        if url is None:
            import of_ship
            url = of_ship.config().get("server_url")
        if not url:
            return "none"
        req = urllib.request.Request(url.rstrip("/") + "/v1/health")
        try:
            import of_client
            for k, v in of_client.access_headers(url).items():
                req.add_unredirected_header(k, v)
        except Exception:  # noqa: BLE001 - no access token configured: a plain request
            pass
        t0 = time.monotonic()
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            r.read()
        return time.monotonic() - t0
    except Exception:  # noqa: BLE001 - no answer within the limit is the reading
        return None


def server_checks(url=None, timeout=5.0, opener=None, token=None):
    """of-server's own checks (GET /v1/health/checks, an authenticated endpoint; docs/health.md §3), as {check: record}
    with the limit as [degraded, failed] and source "of-server", or {} when it does not answer. The machine's token from
    of_client, one try with a short timeout (this runs every minute). Its events and fyi are the server's."""
    import urllib.request
    try:
        if url is None:
            import of_ship
            url = of_ship.config().get("server_url")
        if not url:
            return {}
        import of_client
        if token is None and opener is None:
            token = of_client.Client(url).token()
        req = urllib.request.Request(url.rstrip("/") + "/v1/health/checks")
        secret = dict(of_client.access_headers(url), **({"Authorization": f"Bearer {token}"} if token else {}))
        for k, v in secret.items():  # never sent on to a redirect's host
            req.add_unredirected_header(k, v)
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
            body = json.loads(r.read() or b"{}")
    except Exception:  # noqa: BLE001 - no answer: its checks are not on this machine's page this minute
        return {}
    out = {}
    for c in body.get("checks") or []:
        if isinstance(c, dict) and c.get("check") and c.get("state") in ("ok", "degraded", "failed"):
            out[c["check"]] = {"value": c.get("value"), "limit": [c.get("limit_degraded"), c.get("limit_failed")],
                               "state": c["state"], "owner": c.get("owner") or "of_server", "since": c.get("since"),
                               "source": "of-server"}
    return out


def _read_only(path=None):
    import sqlite3
    import of_store
    conn = sqlite3.connect(f"file:{path or of_store.db_path()}?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA query_only = 1")
    return conn


def listener_lags(now, conn=None, ticks=None):
    """{processor: seconds behind}: from the oldest event after its cursor (0 when it has none, however long ago it
    last moved), or past its timer's period plus its last tick, whichever is later."""
    out = {}
    own = conn is None
    try:
        conn = conn or _read_only()
    except Exception:  # noqa: BLE001 - no store yet
        return out
    try:
        rows = conn.execute("SELECT processor, last_rowid, updated_at FROM listener_cursor").fetchall()
        for name, last, moved in rows:
            first = conn.execute("SELECT ts FROM event WHERE rowid > ? ORDER BY rowid LIMIT 1", (last or 0,)).fetchone()
            lag = 0.0
            if first:  # from the later of the waiting event's time and the cursor's last move: an event imported
                # with an old time is not a week of lag (2026-11-14: 626,876 s right after a restart)
                lag = max(0.0, now - max(_epoch(first[0]), _epoch(moved or "")))
            t = (ticks or {}).get(name)
            if t and t.get("every"):
                lag = max(lag, now - t["at"] - t["every"])
            out[name] = round(lag, 1)
    except Exception:  # noqa: BLE001 - the listener's tables are not there yet
        pass
    finally:
        if own:
            conn.close()
    return out


def _epoch(ts):
    import calendar
    try:
        return calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def _tail_since(path, since_iso, max_bytes=16 << 20):
    """The lines of an append-only log whose leading ISO time is at or after since_iso, read from the end."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    out, pos, buf = [], size, b""
    with open(path, "rb") as fh:
        while pos > 0 and size - pos < max_bytes:
            step = min(65536, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
            lines = buf.split(b"\n")
            buf = lines[0]
            done = False
            for ln in reversed(lines[1:]):
                s = ln.decode(errors="replace")
                m = re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", s[:60])
                ts = m.group(0) if m else ""
                if ts and ts < since_iso[:19]:
                    done = True
                    break
                if s.strip():
                    out.append(s)
            if done:
                break
    return list(reversed(out))


def hook_readings(now, state=None):
    """(error share, worst p99 seconds, {event: p99 seconds}, calls) over the last WINDOW_S, from hook-timing.jsonl
    (one line a call) and hook-errors.log (an ERROR or a spooled call per line)."""
    state = state or of_events.STATE_DIR
    since = iso(now - WINDOW_S)
    calls, by = 0, {}
    for s in _tail_since(os.path.join(state, "hook-timing.jsonl"), since):
        try:
            d = json.loads(s)
        except ValueError:
            continue
        calls += 1
        by.setdefault(d.get("event") or "?", []).append(float(d.get("ms") or 0) / 1000)
    bad = sum(1 for s in _tail_since(os.path.join(state, "hook-errors.log"), since)
              if " ERROR" in s or " spooled" in s)
    p99 = {}
    for ev, xs in by.items():
        xs.sort()
        p99[ev] = round(xs[min(len(xs) - 1, int(0.99 * len(xs)))], 3)
    share = (bad / calls) if calls else 0.0
    return round(share, 4), max(p99.values() or [0.0]), p99, calls


def swap_share(run=subprocess.run):
    """Swap used out of swap (0 when there is none)."""
    try:
        if sys.platform == "darwin":
            # by its path: launchd's PATH for the job has no /usr/sbin (a None reading, 2026-11-14)
            out = run(["/usr/sbin/sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=10).stdout
            m = re.search(r"total = ([\d.]+)M\s+used = ([\d.]+)M", out)
            total, used = (float(m.group(1)), float(m.group(2))) if m else (0.0, 0.0)
        else:
            info = {}
            with open("/proc/meminfo") as fh:
                for ln in fh:
                    k, _, v = ln.partition(":")
                    info[k] = float(v.split()[0]) if v.split() else 0.0
            total, used = info.get("SwapTotal", 0.0), info.get("SwapTotal", 0.0) - info.get("SwapFree", 0.0)
        return round(used / total, 3) if total else 0.0
    except Exception:  # noqa: BLE001
        return None


def free_disk_gib(path=None):
    return round(shutil.disk_usage(path or os.path.expanduser("~")).free / 2 ** 30, 1)


# ------------------------------------------------------------------ one run

def measure(now=None, server=None, lags=None, hooks=None, swap=None, disk=None, smoke=None, views=None,
            served=None):
    """{check: {value, limit, state, owner, detail?}} for this machine. Each reading can be given (tests)."""
    now = time.time() if now is None else now
    out = {}

    def put(check, value, degraded, failed, direction, owner, detail=None):
        out[check] = {"value": value, "limit": [degraded, failed], "state": grade(value, degraded, failed, direction),
                      "owner": owner, **({"detail": detail} if detail else {})}
    s = server_health() if server is None else server
    if s != "none":
        put("server.health", None if s is None else round(s, 2), *LIMITS["server.health"])
    for name, lag in (listener_lags(now, ticks=_ticks()) if lags is None else lags).items():
        put(f"listener.lag.{name}", lag, *LISTENER_LAG, "above", "of_platform")
    for name, (age, lag_ok) in (_view_ages(now) if views is None else views).items():
        put(f"view.age.{name}", age, lag_ok, 5 * lag_ok, "above", "of_platform")
    share, worst, p99, calls = hook_readings(now) if hooks is None else hooks
    put("hooks.errors", share, *LIMITS["hooks.errors"], detail=f"{calls} calls in 10 min")
    put("hooks.latency", worst, *LIMITS["hooks.latency"],
        detail=", ".join(f"{k} {v:.2f}s" for k, v in sorted(p99.items(), key=lambda x: -x[1])[:4]))
    sw = swap_share() if swap is None else swap
    if sw is not None:  # no reading is no finding: the machine's own tools failed, not its memory
        put("machine.memory", sw, *LIMITS["machine.memory"])
    put("machine.disk", free_disk_gib() if disk is None else disk, *LIMITS["machine.disk"])
    for check, r in (server_checks() if served is None else served).items():  # of-server's own, as it serves them
        out.setdefault(check, r)  # this machine's server.health (its answer time here) stays where both have one
    sm = _smoke_reading() if smoke is None else smoke
    if sm is not None:
        out["commit.smoke"] = {"value": str(sm.get("head") or "")[:12], "limit": None, "state": "ok" if sm.get("ok") else "failed",
                               "owner": "of_platform", "detail": sm.get("detail") or ""}
    return out


def _ticks():
    try:
        with open(os.path.join(of_events.STATE_DIR, "listen-ticks.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _view_ages(now):
    """{view: (age seconds, its acceptable lag)} for the views readers trust (of_listen.SERVES_READERS)."""
    try:
        import of_listen
        lags = listener_lags(now)
        return {v: (lags.get(v, 0.0), float(getattr(of_listen, "LAG_OK_S", {}).get(v, 120)))
                for v in of_listen.SERVES_READERS if v in lags}
    except Exception:  # noqa: BLE001
        return {}


def _smoke_reading(repo=None):
    """The smoke result of the working copy's newest commit, running the check when that commit is new."""
    repo = repo or os.path.dirname(HERE)
    try:
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001 - not a git checkout: an installed package has no commits to check
        return None
    if not head:
        return None
    try:
        with open(smoke_path()) as fh:
            last = json.load(fh)
        if last.get("head") == head:
            return last
    except (OSError, ValueError):
        pass
    ok, detail = smoke(repo, ref=head)
    rec = {"head": head, "ok": ok, "detail": detail, "at": iso(time.time())}
    try:
        with open(smoke_path() + ".tmp", "w") as fh:
            json.dump(rec, fh)
        os.replace(smoke_path() + ".tmp", smoke_path())
    except OSError:
        pass
    return rec


def load_state():
    try:
        with open(state_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def run(now=None, readings=None, emit=None, fyi=None, log=print, cards=None):
    """One run: measure, compare with health.json, record each change, tell owners. Returns the new state."""
    now = time.time() if now is None else now
    emit = emit or of_events.emit
    fyi = fyi if fyi is not None else _fyi
    old = load_state().get("checks") or {}
    new = measure(now) if readings is None else readings
    for check, r in new.items():
        prev = old.get(check) or {}
        if r.get("source") == "of-server":  # the server records and tells its own checks' changes
            r["since"] = r.get("since") or (prev.get("since") if prev.get("state") == r["state"] else iso(now))
            continue
        r["since"] = prev.get("since") if prev.get("state") == r["state"] else iso(now)
        if prev.get("state") != r["state"] and (prev or r["state"] != "ok"):
            try:
                emit("health_changed", {"check": check, "state": r["state"], "value": r["value"],
                                        "limit": r["limit"], "since": r["since"], "owner": r["owner"],
                                        "was": prev.get("state")}, position="of_platform")
            except Exception as e:  # noqa: BLE001 - the state file still says it
                log(f"of health: {check}: not recorded ({type(e).__name__})")
        # the owner is told once a failure has lasted TELL_AFTER_S, and once when a told failure is over (2026-11-14:
        # swap around 95% failed and recovered every few minutes, one --fyi each time)
        r["told"] = prev.get("told") if r["state"] == "failed" else None
        if r["state"] == "failed" and not r["told"] and now - _epoch(r["since"]) >= TELL_AFTER_S:
            fyi(r["owner"], _fyi_text(check, r, prev))
            r["told"] = iso(now)
        elif r["state"] != "failed" and prev.get("told"):
            fyi(r["owner"], _fyi_text(check, r, prev))
    for check in set(old) - set(new):  # a check that stopped applying (a processor gone) leaves quietly
        pass
    state = {"at": iso(now), "checks": new}
    with open(state_path() + ".tmp", "w") as fh:
        json.dump(state, fh, indent=1)
    os.replace(state_path() + ".tmp", state_path())
    if cards is None or cards:
        try:  # the person's card when their own work is hit (of_studio's, docs/health.md §3; hand-over f06e0eb7)
            import of_studio_health
            (cards or of_studio_health.cards)(state)
        except Exception as e:  # noqa: BLE001 - the page and the events stand without it
            log(f"of health: cards: {type(e).__name__}: {e}")
    return state


def _fyi_text(check, r, prev):
    if r["state"] == "failed":
        return (f"Health: {check} failed on this machine at {r['since']}: value {r['value']}, limit {r['limit']} "
                f"{('(' + r['detail'] + ')') if r.get('detail') else ''} (docs/health.md §2; `of health`).")
    return f"Health: {check} is {r['state']} since {r['since']} (value {r['value']}); the failure you were told of " \
           f"is over."


def _fyi(owner, text):
    """One --fyi to the check's owner, from of_platform, when the OF team's organization is on this machine."""
    if TEAM_ORG not in _org_names():
        return
    subprocess.run([sys.executable, os.path.join(HERE, "of"), "send", f"{TEAM_ORG}:{owner}", "--from", FYI_FROM,
                    "--fyi", text], capture_output=True, text=True, timeout=60,
                   env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))


def _org_names():
    """The organizations listed on this machine, from orgs.yaml alone (parsing the registries cost 4 s a run)."""
    import yaml
    path = os.environ.get("OF_ORGS") or os.path.join(os.environ.get("OF_CONFIG_DIR") or
                                                     os.path.expanduser("~/.config/of"), "orgs.yaml")
    try:
        with open(path) as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return set()
    orgs = doc.get("orgs") or []
    return {str(o.get("name")) for o in (orgs if isinstance(orgs, list) else []) if isinstance(o, dict)}


def table(state):
    rows = [("check", "state", "value", "limit", "since", "owner")]
    for check, r in sorted((state.get("checks") or {}).items(), key=lambda x: ({"failed": 0, "degraded": 1}.get(
            x[1].get("state"), 2), x[0])):
        lim = r.get("limit")
        rows.append((check, r.get("state", "?"), str(r.get("value")), "" if lim is None else f"{lim[0]} / {lim[1]}",
                     r.get("since") or "", r.get("owner") or ""))
    widths = [max(len(x[i]) for x in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(v.ljust(w) for v, w in zip(row, widths)).rstrip() for row in rows]
    return "\n".join(lines + [f"as of {state.get('at') or 'never'} (docs/health.md §2)"])


# ------------------------------------------------------------------ the smoke check (§4)

SMOKE_EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")


def smoke(repo, ref=None, index=False, timeout=120):
    """(ok, detail) for a commit's content, in a scratch copy and a scratch store: every module in bin/ imports,
    `of hook` takes a synthetic payload of each event kind, and `of status`, `of asks` and `of who` answer.
    ref: a commit to check; index: the content being committed (the pre-commit gate, which sets GIT_INDEX_FILE)."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="of-smoke-") as tmp:
        tree = os.path.join(tmp, "tree")
        os.makedirs(tree)
        try:
            if index:
                subprocess.run(["git", "-C", repo, "checkout-index", "-a", f"--prefix={tree}/"], check=True,
                               capture_output=True, timeout=60)
            else:
                arch = subprocess.run(["git", "-C", repo, "archive", ref or "HEAD", "bin"], check=True,
                                      capture_output=True, timeout=60).stdout
                subprocess.run(["tar", "-x", "-C", tree], input=arch, check=True, capture_output=True, timeout=60)
        except Exception as e:  # noqa: BLE001
            return False, f"could not lay out the commit: {type(e).__name__}"
        bin_dir = os.path.join(tree, "bin")
        state, home = os.path.join(tmp, "state"), os.path.join(tmp, "home")
        os.makedirs(state)
        os.makedirs(home)
        orgs = os.path.join(tmp, "orgs.yaml")  # one organization with one position, for status, asks and who
        os.makedirs(os.path.join(tmp, "org", "registry"))
        with open(os.path.join(tmp, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write("positions:\n  - {name: smoke_agent, system: smoke, role: agent, kind: project, owner: founder, "
                     "summary: the smoke check, status: active}\n")
        with open(orgs, "w") as fh:
            fh.write(f"orgs:\n  - name: smoke\n    path: {tmp}/org\n    workspace: smoke\n")
        import site
        env = dict(os.environ, OF_STATE=state, OF_ORGS=orgs, HOME=home, OF_AUTOMATION="1", SSH_AUTH_SOCK="",
                   PYTHONUSERBASE=site.getuserbase(),  # PyYAML may be a user install: a scratch HOME must not hide it
                   OF_CONFIG_DIR=os.path.join(tmp, "claude"), GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        for k in ("CLAUDE_CODE_SESSION_ID", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "HERDR_ENV", "GIT_INDEX_FILE",
                  "GIT_DIR", "GIT_WORK_TREE"):
            env.pop(k, None)
        mods = sorted(f[:-3] for f in os.listdir(bin_dir) if f.endswith(".py") and f.startswith("of_"))
        code = ("import importlib, sys; sys.path.insert(0, %r); bad = []\n"
                "for m in %r:\n"
                "    try: importlib.import_module(m)\n"
                "    except Exception as e: bad.append(f'{m}: {type(e).__name__}: {e}'[:200])\n"
                "print('\\n'.join(bad)); sys.exit(1 if bad else 0)") % (bin_dir, mods)
        steps = [("imports", [sys.executable, "-c", code], None)]
        for ev in SMOKE_EVENTS:
            payload = {"hook_event_name": ev, "session_id": "of-smoke", "cwd": tmp, "prompt": "smoke",
                       "tool_name": "Bash", "tool_input": {"command": "true"}, "tool_response": {"stdout": ""},
                       "transcript_path": os.path.join(tmp, "none.jsonl")}
            steps.append((f"hook {ev}", [sys.executable, os.path.join(bin_dir, "of"), "hook", ev], json.dumps(payload)))
        for args in (["status", "working", "--note", "smoke", "--from", "smoke_agent"], ["asks"], ["who", "--json"]):
            steps.append(("of " + args[0], [sys.executable, os.path.join(bin_dir, "of")] + args, None))
        end = time.monotonic() + timeout
        for name, argv, stdin in steps:
            try:
                r = subprocess.run(argv, input=stdin, capture_output=True, text=True, env=env, cwd=tmp,
                                   timeout=max(5, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                return False, f"{name}: no answer within the smoke check's {timeout} s"
            if r.returncode != 0 or "Traceback (most recent call last)" in (r.stderr or ""):
                why = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
                return False, f"{name}: exit {r.returncode}: {(why[-1] if why else '')[:300]}"
    return True, f"{len(mods)} modules import; {len(SMOKE_EVENTS)} hooks, status, asks and who answer"


# ------------------------------------------------------------------ the service

def plist(python=None, of_bin=None):
    from xml.sax.saxutils import escape
    python = python or sys.executable
    of_bin = of_bin or os.path.join(HERE, "of")
    log = os.path.join(of_events.STATE_DIR, "health.log")
    args = "".join(f"<string>{escape(a)}</string>" for a in (python, of_bin, "health", "run"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>EnvironmentVariables</key><dict><key>OF_AUTOMATION</key><string>1</string><key>SSH_AUTH_SOCK</key><string></string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>ProcessType</key><string>Standard</string>
  <key>StartInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>{escape(log)}</string>
  <key>StandardErrorPath</key><string>{escape(log)}</string>
</dict></plist>
"""


def install(uninstall=False, run=subprocess.run, agents_dir=None):
    """`of health install|uninstall`: the launchd job every minute at Standard priority (docs/health.md §5)."""
    import of_listen
    return of_listen.install_launchd(uninstall=uninstall, run=run, plists={LABEL: plist()}, agents_dir=agents_dir)
