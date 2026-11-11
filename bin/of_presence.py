"""Who holds what on the organization's other machines (docs/server-several-machines.md §4.3, §4.4).

Every machine's relay publishes the positions it holds (`PUT /v1/presence`). This module reads them back
(`GET /v1/presence?org=`) and keeps the holders that are on another machine. The pulse uses them so that only the
machine that holds a position acts on it and records events about it; `of who` lists them with machine, person
and age.

Best effort: a server that does not answer gives no remote holders, so a machine alone, or cut off, acts as it did
before (on what it holds itself). A short disk cache keeps the pulse and `of who` from asking more than once a
minute.
"""

import json
import os
import time

import of_events

CACHE_S = 60
TIMEOUT_S = 2


def _cache_path():
    return os.path.join(of_events.STATE_DIR, "presence-remote.json")


def _servers():
    import of_ship
    cfg = of_ship.config()
    return of_ship.org_servers(cfg), cfg["server_url"].rstrip("/")


def _fetch_org(org, url, client_for, timeout_s):
    if client_for:
        c = client_for(url)
    else:
        import of_client
        c = of_client.Client(url)
        c.opener = lambda req, timeout=None, context=None, _o=c.opener: _o(req, timeout=timeout_s, context=context)
    pres = c.presence(org)
    return (pres.get("holders") if isinstance(pres, dict) else pres) or []


def remote(orgs, me=None, servers=None, default=None, client_for=None, timeout_s=TIMEOUT_S, cache_s=CACHE_S,
           now=None):
    """{org: [holder]}: the holders of each organization on machines other than this one. A holder is the server's
    row: machine_id, position, session_id, state, pane, kind, age_s, person. An organization whose server did not
    answer is missing from the result."""
    now = now or time.time()
    me = me or of_events.machine_id()
    orgs = sorted({o for o in orgs if o})
    cached = {}
    if cache_s:
        try:
            with open(_cache_path()) as fh:
                doc = json.load(fh) or {}
            if doc.get("me") == me and now - float(doc.get("at") or 0) < cache_s:
                cached = doc.get("orgs") or {}
        except (OSError, ValueError, TypeError):
            cached = {}
    if all(o in cached for o in orgs):
        return {o: cached[o] for o in orgs}
    if servers is None or default is None:
        try:
            servers, default = _servers()
        except Exception:  # noqa: BLE001 - no server configured: nothing is remote
            return {}
    out = {}
    for org in orgs:
        try:
            hs = _fetch_org(org, servers.get(org, default), client_for, timeout_s)
        except Exception:  # noqa: BLE001 - unreachable or not joined: no remote holders for it
            continue
        out[org] = [h for h in hs if h.get("machine_id") and h.get("machine_id") != me]
    if cache_s and out:
        try:
            tmp = _cache_path() + f".{os.getpid()}"
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                json.dump({"me": me, "at": now, "orgs": out}, fh)
            os.replace(tmp, _cache_path())
        except OSError:
            pass
    return out


def elsewhere(positions, local_names=(), **kw):
    """{(org, position): holder} for registry positions held on another machine now and not held here. A position
    held both here and elsewhere (a stale presence row, a session moved between machines) counts as held here."""
    local = set(local_names)
    names = {(p.get("_org"), p["name"]) for p in positions}
    out = {}
    for org, hs in remote({o for o, _ in names}, **kw).items():
        for h in hs:
            k = (org, h.get("position"))
            if k in names and k[1] not in local and k not in out:
                out[k] = h
    return out


def age_text(s):
    try:
        s = float(s)
    except (TypeError, ValueError):
        return "?"
    return f"{int(s)}s" if s < 120 else f"{int(s // 60)}m" if s < 7200 else f"{int(s // 3600)}h"
