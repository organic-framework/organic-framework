"""The model of an organization at time t (docs/studio.md §2).

Pure functions over a registry document and typed event rows. No herdr, no network.
The terminal, the query (`of model`) and the web (bin/of_studio.py) all read this module.

  registry_at(repo, t)        the registry document at t: the last commit at or before t, else the working file
  build(doc, events, at, ...) MODEL(t): positions, systems, owners and units, system DSM, DMM, position DSM, coverage
  snapshot(model)             the model as stable JSON for the organization's repository (names and numbers only)
  cells_of(...)               RFC 0007 §2.3: ownership cells (system x kind of work), gaps and overlaps; model["cells"]
  code_activity(model, rows, a, b)   RFC 0007 §2.2: per-system code activity, writers and co-change in [a, b)
  evidence(model, activity)   RFC 0007 §2.2: each system's runtime, code, money and governance evidence
"""

import calendar
import collections
import functools
import hashlib
import json
import os
import re
import subprocess
import time

HALF_LIFE_H = 72.0          # position DSM decay (docs/studio.md §2.2)
MAX_SYSTEMS = 3             # logical coupling: a PR touching more systems is a cross-system change, not coupling
HUMAN = "founder"           # the root owner when a chain ends outside the registry
PEER_KINDS = ("of.agent_message_out", "of.agent_message_in")
READ_TOOLS = ("Read", "Grep", "Glob")
MIN_ROUTER = 2   # of.mandate_check verdicts count from rt-v2 (of_router.VERSION; worktrees and unscoped, 2026-11-06)
MIN_ROUTER_UNOWNED = 4   # an 'unowned' verdict counts from rt-v4: before it, linked worktrees outside the repository
                         # (<repo>-wt/<name>, <repo>-wt-<name>, .worktrees/<other name>) were judged unowned (2026-11-07)


def router_version(pl):
    """The mandate router's version of a verdict: 'rt-v2' -> 2; absent -> 1 (rt-v1)."""
    m = re.match(r"rt-v(\d+)$", str(pl.get("router_version") or "rt-v1"))
    return int(m.group(1)) if m else 1


def area_of(path, depth=3, repos=()):
    """A path no system claims, as a short area: its first directories under the home ('~/acme/storefront').
    A path in a linked git worktree is its repository's (a user organization's structure keeper, 2026-11-12: six
    edits in worktrees showed as the area '~/<org>/.worktrees'): by the worktree's .git file while the worktree is
    there (of_router.worktree_main), else by the folder layouts the router knows (of_router.unworktree, with
    `repos` the repository names the map claims). A `.worktrees/<name>` whose repository is not known keeps its name."""
    import of_router
    p = str(path or "")
    full = os.path.expanduser(p)
    wt = of_router.worktree_main(full) if os.path.isabs(full) else None
    if wt:
        p, home = wt[1] + full[len(wt[0]):], os.path.expanduser("~")
        p = "~" + p[len(home):] if p.startswith(home + os.sep) else p
    parts = [x for x in p.split("/") if x]
    if not parts:
        return "(no path)"
    if len(parts) > depth:
        parts = parts[:depth - 1] + [x for x in of_router.unworktree("/".join(parts[depth - 1:]), repos).split("/") if x]
        depth += len(parts) >= depth and parts[depth - 1] == ".worktrees"
    return "/".join(parts[:depth])
ASK_KINDS = ("of.agent_question", "of.permission_request")
MODEL_VERSION = 1
# Authors whose work records carry no coupling: merge-queue copies of another PR, and dependency bumps (2026-11-05)
BOT_AUTHOR = re.compile(r"^(app/|dependabot|renovate|github-actions|mergify)|\[bot\]$", re.I)


# ------------------------------------------------------------------ time

def epoch(ts):
    """ISO 8601 UTC ('2026-11-05T15:42:07.123Z' or without Z / fraction) -> seconds; None if unparseable."""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    return _epoch_str(str(ts))


@functools.lru_cache(maxsize=1 << 20)   # the same timestamps are parsed many times per page; parsing dominated
def _epoch_str(ts):
    s = ts.strip().replace("Z", "").replace("+00:00", "")
    frac = 0.0
    if "." in s:
        s, f = s.split(".", 1)
        try:
            frac = float("0." + re.sub(r"\D.*", "", f or "0"))
        except ValueError:
            frac = 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return calendar.timegm(time.strptime(s, fmt)) + frac
        except ValueError:
            continue
    return None


def iso(sec):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(sec))


# ------------------------------------------------------------------ registry at t

def registry_at(repo, at=None, rel="registry/positions.yaml"):
    """Return (doc, rev). rev is the commit id, or 'undated:<sha256-12>' for the working file."""
    import yaml
    repo = os.path.expanduser(repo)
    if at is not None:
        try:
            rev = subprocess.run(["git", "-C", repo, "rev-list", "-1", f"--before={iso(epoch(at))}", "HEAD", "--", rel],
                                 capture_output=True, text=True, timeout=10).stdout.strip()
            if rev:
                text = subprocess.run(["git", "-C", repo, "show", f"{rev}:{rel}"],
                                      capture_output=True, text=True, timeout=10).stdout
                return yaml.safe_load(text) or {}, rev
        except (OSError, subprocess.SubprocessError):
            pass
    path = os.path.join(repo, rel)
    with open(path, "rb") as fh:
        raw = fh.read()
    return yaml.safe_load(raw) or {}, "undated:" + hashlib.sha256(raw).hexdigest()[:12]


def _systems_block(text):
    """The registry's top-level `systems:` block alone (about a quarter of a large registry), or None."""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith("systems:")), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i][:1].isalpha() or lines[i][:1] == "_"), len(lines))
    return "\n".join(lines[start:end])


def _keepers_of(text):
    """{system: keeper} of one registry revision: its `systems:` block parsed alone, else the whole file."""
    import yaml
    doc = None
    block = _systems_block(text)
    if block is not None:
        try:
            doc = yaml.safe_load(block)
        except yaml.YAMLError:
            doc = None
    if not isinstance(doc, dict):
        doc = yaml.safe_load(text) or {}
    return {str(sid): (s or {}).get("keeper") for sid, s in (doc.get("systems") or {}).items() if isinstance(s, dict) or s is None}


def _keepers_cache_path(repo, rel):
    import of_events
    key = hashlib.sha256(f"{os.path.realpath(repo)}\0{rel}".encode()).hexdigest()[:16]
    return os.path.join(of_events.STATE_DIR, "studio", "keepers", key + ".json")


def _save_json(path, obj):
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh)
        os.replace(tmp, path)
    except OSError:
        pass


@functools.lru_cache(maxsize=256)
def _registry_history(repo, head, rel="registry/positions.yaml"):
    """[(commit time, {system: keeper})] of the registry, oldest first (cached per HEAD in the process). A commit never
    changes, so each revision's keepers are kept on disk by commit id: a run parses only the revisions it has not seen
    (a user organization's 179 took 72 s per run, 2026-11-11). Progress is saved every 10 s, so a killed run still keeps it."""
    try:
        revs = subprocess.run(["git", "-C", repo, "log", "--reverse", "--format=%H %ct", "--", rel],
                              capture_output=True, text=True, timeout=20).stdout.split("\n")
    except (OSError, subprocess.SubprocessError):
        return ()
    path = _keepers_cache_path(repo, rel)
    try:
        with open(path) as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    out, dirty, saved = [], False, time.time()
    for line in revs:
        if not line.strip():
            continue
        rev, ct = line.split()
        if rev not in cache:
            try:
                text = subprocess.run(["git", "-C", repo, "show", f"{rev}:{rel}"], capture_output=True, text=True,
                                      timeout=10).stdout
                cache[rev] = _keepers_of(text)
            except Exception:  # noqa: BLE001 — an unparseable old revision is skipped, and remembered as such
                cache[rev] = None
            dirty = True
            if time.time() - saved > 10:
                _save_json(path, cache)
                dirty, saved = False, time.time()
        if cache[rev] is not None:
            out.append((float(ct), cache[rev]))
    if dirty:
        _save_json(path, cache)
    return tuple(out)


def keeper_since(repo, system, keeper):
    """When `keeper` became the keeper of `system` in the registry's history (the first commit of its current run);
    None when unknown (no git history)."""
    repo = os.path.expanduser(repo)
    try:
        head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    since = None
    for ct, keepers in _registry_history(repo, head):
        if keepers.get(system) == keeper:
            since = since or ct
        else:
            since = None
    return since


# ------------------------------------------------------------------ globs (same rule as `of registry check`)

def _class_regex(body):
    """A glob class as a regex class, as bin/of reads it: `[!x]` and `[^x]` are negated and, like `*` and `?`, never
    match `/` (a regex reading of `[!x]` matched only `!` and `x`; a user organization, 2026-11-12)."""
    if body.startswith(("!", "^")):
        return "[^" + body[1:].replace("\\", "\\\\") + "/]"
    return "[" + body.replace("\\", "\\\\") + "]"


def glob_regex(p):
    out, i = [], 0
    while i < len(p):
        if p.startswith("**", i):
            out.append(".*"); i += 2
        elif p[i] == "*":
            out.append("[^/]*"); i += 1
        elif p[i] == "?":
            out.append("[^/]"); i += 1
        elif p[i] == "[" and "]" in p[i + 1:]:
            j = p.index("]", i + 1); out.append(_class_regex(p[i + 1:j])); i = j + 1
        else:
            out.append(re.escape(p[i])); i += 1
    return re.compile("^" + "".join(out) + "$")


class PathMap:
    """Maps '<repo>/<path in repo>' to a system: a `paths` glob first (longest pattern wins), else a whole-repo claim."""

    def __init__(self, systems):
        self.globs, self.repos = [], {}
        for sid, s in (systems or {}).items():
            s = s or {}
            for p in s.get("paths") or []:
                self.globs.append((len(p.replace("*", "")), glob_regex(p), sid))
            if not s.get("paths"):
                for r in s.get("repos") or []:
                    self.repos.setdefault(str(r), sid)
        self.globs.sort(key=lambda g: -g[0])
        self.names = {str(x).split("/")[0] for s in (systems or {}).values()
                      for x in ((s or {}).get("paths") or []) + ((s or {}).get("repos") or [])}

    def repo_names(self):
        """The repository names the map claims: each `paths` glob's first folder and each whole-repo claim."""
        return {n for n in self.names if n and not any(c in n for c in "*?[")}

    def system(self, repo, path=None):
        full = f"{repo}/{path}" if path else str(repo)
        for _, rx, sid in self.globs:
            if rx.match(full):
                return sid
        return self.repos.get(str(repo).rstrip("/").split("/")[-1])


# ------------------------------------------------------------------ owners

def _is_human(p):
    return ((p.get("holder") or {}).get("runtime") == "human")


def accountable(name, positions):
    """The first human up the owner chain of a position. A chain that leaves the registry ends at its last owner."""
    seen, cur = set(), name
    while cur in positions and cur not in seen:
        seen.add(cur)
        p = positions[cur]
        if _is_human(p) and cur != name:
            return cur
        nxt = p.get("owner") or HUMAN
        if nxt not in positions:
            return nxt
        cur = nxt
    return HUMAN


def owner_chain(name, positions):
    out, seen, cur = [], set(), positions.get(name, {}).get("owner")
    while cur and cur not in seen:
        seen.add(cur)
        out.append(cur)
        cur = positions.get(cur, {}).get("owner") if cur in positions else None
    return out


# ------------------------------------------------------------------ build

def org_units(doc):
    """{unit: {name, owner, systems, delegation}} from the registry's `units:` and `delegations:` (docs/registry.md).
    The active delegation of a unit is the last record whose to_owner is the unit's current owner."""
    out = {}
    dels = [d for d in (doc.get("delegations") or []) if isinstance(d, dict)]
    for uid, u in (doc.get("units") or {}).items():
        if not isinstance(u, dict):
            continue
        owner = u.get("owner")
        rec = next((d for d in reversed(dels) if d.get("unit") == uid and d.get("to_owner") == owner), None)
        out[uid] = {"name": u.get("name"), "owner": owner, "systems": list(u.get("systems") or []), "delegation": rec}
    return out


def _positions(doc):
    out = {}
    for p in doc.get("positions") or []:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        h = p.get("holder") or {}
        out[p["name"]] = {
            "system": p.get("system"), "role": p.get("role"), "kind": p.get("kind"),
            "owner": p.get("owner") or HUMAN, "status": p.get("status") or "active",
            "throughput": p.get("throughput"), "waiting_on": p.get("waiting_on"),
            "summary": p.get("summary"), "session": h.get("session"), "runtime": h.get("runtime"),
            "aliases": list(p.get("aliases") or []), "holder": h,
            "exit": p.get("mandate_exit") or p.get("exit"), "functions": p.get("mandate_functions") or p.get("functions"),
            "owns": _owns(p.get("owns")),
        }
    units = org_units(doc)
    unit_of_system = {sysid: uid for uid, u in units.items() for sysid in u["systems"]}
    for n, p in out.items():
        p["accountable"] = accountable(n, {k: {"owner": v["owner"], "holder": v["holder"]} for k, v in out.items()})
        uid = unit_of_system.get(p["system"])
        p["unit"] = uid
        u = units.get(uid) or {}
        # a position of a delegated unit answers to the unit's owner (of_platform's routing of asks, 2026-11-06)
        if u.get("delegation") and u.get("owner") and u["owner"] != n:
            p["accountable_before"] = p["accountable"]   # before the delegation took effect (of.diff_applied)
            p["accountable"] = u["owner"]
            p["delegation"] = u["delegation"].get("id")
            p["delegation_diff"] = u["delegation"].get("diff")
    for p in out.values():
        p.pop("holder", None)
    return out


def _systems(doc):
    out = {}
    for sid, s in (doc.get("systems") or {}).items():
        s = s or {}
        out[sid] = {"name": s.get("name"), "kind": s.get("kind"), "parent": s.get("parent"),
                    "keeper": s.get("keeper"), "governed_by": s.get("governed_by"),
                    "lifecycle": s.get("lifecycle") or "live",   # live (default) | unsupported | retired (registry.md)
                    # awaiting the owner's confirm pass: true (the entry) or a list of what, e.g. [keeper, grain]
                    "confirm": s.get("confirm") if isinstance(s.get("confirm"), list) else bool(s.get("confirm")),
                    "doubt": s.get("doubt"),
                    # RFC 0007 §2.2-2.3: the system architect's evidence and the kinds of work the system needs
                    "runs": _runs(s.get("runs")), "money": s.get("money"),
                    "confirmed": {str(k): str(v) for k, v in s["confirmed"].items()} if isinstance(s.get("confirmed"), dict) else {},
                    "needs": [str(x) for x in s.get("needs") or [] if x] if isinstance(s.get("needs"), list) else [],
                    "paths": [str(x) for x in s.get("paths") or []], "repos": [str(x) for x in s.get("repos") or []],
                    "depends_on": [{"system": e.get("system"), "kind": e.get("kind"), "interface": e.get("interface"),
                                    "observed_by": e.get("observed_by"), "evidence": e.get("evidence")}
                                   for e in (s.get("depends_on") or []) if isinstance(e, dict)]}
    return out


def delegation_effective(rows):
    """{diff id: epoch}: when each structural diff of kind `delegate` was applied (of.diff_applied)."""
    out = {}
    for r in rows:
        if r.get("name") == "of.diff_applied":
            pl = r.get("payload") or {}
            if pl.get("kind") == "delegate" and pl.get("id"):
                out.setdefault(pl["id"], epoch(r.get("ts")))
    return out


def accountable_at(p, t, effective):
    """A position's accountable owner at time t: the unit owner once its delegation took effect, before it the
    owner chain. A delegation with no recorded application counts from the start (the registry says it holds)."""
    if p.get("delegation") and p.get("accountable_before"):
        since = effective.get(p.get("delegation_diff"))
        if since is not None and t is not None and t < since:
            return p["accountable_before"]
    return p["accountable"]


def session_index(positions):
    idx = {}
    for n, p in positions.items():
        if p.get("session"):
            idx[p["session"]] = n
    return idx


def position_of(row, positions, sessions):
    """Name an event's position: the event's own (gen_ai.agent.name), else the holder session, else None."""
    pos = row.get("position")
    if pos in positions:
        return pos
    return sessions.get(row.get("session_id"))


def _runs(v):
    """`runs:` as {where, evidence}: a map, or a bare string naming where."""
    if isinstance(v, dict):
        return {"where": str(v.get("where")) if v.get("where") is not None else None, "evidence": v.get("evidence")}
    return {"where": str(v), "evidence": None} if v else None


def _owns(v):
    """A position's `owns:` as [{system, method, decides}] (RFC 0007 §2.3); malformed entries are left out."""
    out = []
    for x in v or [] if isinstance(v, list) else []:
        if isinstance(x, dict) and x.get("system") and x.get("method"):
            out.append({"system": str(x["system"]), "method": str(x["method"]), "decides": x.get("decides") is True})
    return out


# ------------------------------------------------------------------ cells and evidence (RFC 0007)

# The framework's default kinds of work (RFC 0007 §2.3); an organization adds its own under a top-level `methods:` map.
METHODS = {
    "research": "opportunities, losses, hypotheses and studies",
    "model": "quantitative models: pricing, bidding, prediction, the money value of an effect",
    "build": "code and design",
    "verify": "backtests, tests, validation before a change runs",
    "operate": "releases, configuration, deploys, on-call, incidents",
    "measure": "timing, metrics, monitoring",
    "account": "ledger, P&L, costs, attribution",
    "secure": "security, secrets, risk limits, compliance",
    "data": "capture, storage and serving",
}
GOVERNANCE_FACTS = ("keeper", "governed_by", "lifecycle", "boundary")   # RFC 0007 §2.2: confirmed by an ask id
OVERLAP_DOERS = 3   # a cell with this many doers or more is shown as an overlap (RFC 0007 §2.3)


def methods_of(doc):
    """The organization's kinds of work: the defaults, then its own `methods:` (name -> one-line definition)."""
    out = dict(METHODS)
    for k, v in ((doc or {}).get("methods") or {}).items() if isinstance((doc or {}).get("methods"), dict) else []:
        out[str(k)] = str(v or "")
    return out


def governance(s):
    """A system's governance facts (RFC 0007 §2.2) as [{fact, value, required, ask}]. `required`: the facts
    `of registry check` warns on without an ask id: a keeper, a non-`self` governed_by, a non-default lifecycle.
    Only `confirmed:` confirms; a comment such as "(founder, date)" never does. `boundary` is listed once confirmed."""
    conf = s.get("confirmed") or {}
    gov, lc = s.get("governed_by"), s.get("lifecycle") or "live"
    facts = [("keeper", s.get("keeper"), bool(s.get("keeper"))),
             ("governed_by", gov, bool(gov) and gov != "self"),
             ("lifecycle", lc, lc != "live"),
             ("boundary", None, False)]
    return [{"fact": f, "value": v, "required": req, "ask": conf.get(f)} for f, v, req in facts if req or conf.get(f)]


def ask_found(ask, ids):
    """Whether an ask id names one of `ids` (event ids of asks): the whole id, or either 8-character form."""
    ask = str(ask or "").strip()
    if not ask:
        return False
    if ask in ids:
        return True
    return len(ask) >= 8 and any(i.startswith(ask) or i.endswith(ask) for i in ids)


def cells_of(positions, systems, methods):
    """RFC 0007 §2.3: one row per cell (system x kind of work) that a system needs or a position owns.
    Only active positions own: a parked decider leaves its cell without an owner. Rows:
    {system, method, needed, live, deciders, doers (every active position on the cell, the decider too), inactive,
    gap: None | "nobody" | "no_decider" (a needed cell of a live system), overlap (doers >= OVERLAP_DOERS)}.
    A cell on a parent system does not cover its subsystems' cells: each is its own row."""
    rows, unknown = {}, []

    def row(sid, m):
        s = systems.get(sid) or {}
        return rows.setdefault((sid, m), {"system": sid, "method": m, "needed": False,
                                          "live": (s.get("lifecycle") or "live") == "live",
                                          "deciders": [], "doers": [], "inactive": []})
    for sid, s in systems.items():
        for m in s.get("needs") or []:
            if m in methods:
                row(sid, m)["needed"] = True
            else:
                unknown.append({"position": None, "system": sid, "method": m, "why": "unknown kind of work in needs"})
    for n, p in sorted(positions.items()):
        for o in p.get("owns") or []:
            why = ("unknown system" if o["system"] not in systems else None) or \
                  ("unknown kind of work" if o["method"] not in methods else None)
            if why:
                unknown.append({"position": n, "system": o["system"], "method": o["method"], "why": why})
                continue
            r = row(o["system"], o["method"])
            if p.get("status") != "active":
                r["inactive"].append(n)
                continue
            if n not in r["doers"]:
                r["doers"].append(n)
            if o["decides"] and n not in r["deciders"]:
                r["deciders"].append(n)
    order = {m: i for i, m in enumerate(methods)}
    out = sorted(rows.values(), key=lambda r: (r["system"], order.get(r["method"], 99)))
    for r in out:
        r["gap"] = ("nobody" if not r["doers"] else "no_decider") if r["needed"] and r["live"] and not r["deciders"] else None
        r["overlap"] = len(r["doers"]) >= OVERLAP_DOERS
    return {"methods": methods, "cells": out,
            "gaps": [r for r in out if r["gap"]], "overlaps": [r for r in out if r["overlap"]],
            "two_deciders": [r for r in out if len(r["deciders"]) > 1], "unknown": unknown,
            "systems_with_needs": sorted(sid for sid, s in systems.items() if s.get("needs")),
            "positions_with_owns": sorted(n for n, p in positions.items() if p.get("owns"))}


def _touched(payload):
    """[(repo, path)] from a work_record payload: `repo` + `paths`, or `touched: [{repo, path}]`."""
    out = []
    repo = payload.get("repo") or payload.get("repository")
    if isinstance(repo, str):
        repo = repo.rstrip("/").split("/")[-1]
    for t in payload.get("touched") or []:
        if isinstance(t, dict):
            out.append((t.get("repo") or repo, t.get("path")))
    for p in payload.get("paths") or []:
        out.append((repo, p))
    if not out and repo:
        out.append((repo, None))
    return out


def pr_key(pl):
    """(repo name, PR number) of a work record: from `url` (github.com/<owner>/<repo>/pull/<n>), else repo + number."""
    m = re.search(r"github\.com/[^/]+/([^/]+)/pull/(\d+)", str(pl.get("url") or ""))
    if m:
        return m.group(1).lower(), int(m.group(2))
    repo, num = pl.get("repo") or pl.get("repository"), pl.get("number")
    if repo and num is not None:
        try:
            return str(repo).rstrip("/").split("/")[-1].lower(), int(num)
        except (TypeError, ValueError):
            return None
    return None


def pr_attribution(events, positions, sessions):
    """{(repo, number): position} from transcript pr-links (of_field-analyst, 2026-11-06): a pr-link is an attribution
    fact about a merged PR, not a work record of its own. The session's position comes from the event, the holder
    index, or any other event of that session that names a registered position. The earliest link wins."""
    sess_pos = {}
    for r in events:
        if r.get("position") in positions and r.get("session_id"):
            sess_pos.setdefault(r["session_id"], r["position"])
    out = {}
    for r in sorted((r for r in events if r.get("name") == "of.work_record"
                     and (r.get("payload") or {}).get("source") == "pr-link"), key=lambda r: epoch(r.get("ts")) or 0):
        pl = r.get("payload") or {}
        pos = pl.get("position") if pl.get("position") in positions else None
        pos = pos or position_of(r, positions, sessions) or sess_pos.get(r.get("session_id"))
        k = pr_key(pl)
        if k and pos and k not in out:
            out[k] = pos
    return out


def record_writer(r, positions, sessions, attrib):
    """The position that wrote a work record (or an open PR's of.pr_state): the session's holder, the payload's
    position, else a transcript pr-link's attribution."""
    pl = r.get("payload") or {}
    return position_of(r, positions, sessions) or (pl.get("position") if pl.get("position") in positions else None) \
        or attrib.get(pr_key(pl))


def record_systems(pl, systems, pmap):
    """The systems a work record touched, None standing for a path no system claims: its `systems` mapped at import
    by changed paths, else its paths mapped here (of import git before the map named paths, or none given)."""
    if isinstance(pl.get("systems"), list) and (pl["systems"] or not (pl.get("paths") or pl.get("touched"))):
        return {x for x in pl["systems"] if x in systems} or {None}
    return {pmap.system(repo, path) for repo, path in _touched(pl) if repo}


def build(doc, events=(), at=None, org=None, rev=None, window_h=None, half_life_h=HALF_LIFE_H, max_systems=MAX_SYSTEMS):
    """MODEL(t). events: rows {event_id, ts, name, session_id, position, payload} (any order).
    Only events at or before `at` count; work records within the last `window_h` hours (default: all) give coupling and writes."""
    at_s = epoch(at) if at is not None else time.time()
    lo = at_s - window_h * 3600 if window_h else None
    positions, systems = _positions(doc), _systems(doc)
    sessions = session_index(positions)
    pmap = PathMap(doc.get("systems"))
    map_repos = pmap.repo_names()

    dsm = {}
    for sid, s in systems.items():
        for e in s["depends_on"]:
            if e["system"]:
                dsm[(sid, e["system"])] = {"declared": e["kind"] or "uses", "interface": e["interface"],
                                           "observed_by": e.get("observed_by"), "coupling": 0, "coupling_k": {}}
    dmm = {}

    def cell(p, s):
        return dmm.setdefault((p, s), {"home": False, "keeper": False, "writes": 0, "touches": 0, "edits": 0})

    mandate = {}   # position -> verdict counts from the mandate router (observe mode)
    orphan = {}    # area (a system without keeper, or a directory no system claims) -> {position: calls}

    for n, p in positions.items():
        if p["system"] in systems:
            cell(n, p["system"])["home"] = True
    for sid, s in systems.items():
        if s["keeper"]:
            cell(s["keeper"], sid)["keeper"] = True

    pdsm = {}
    attrib = pr_attribution(events, positions, sessions)

    unmapped = bots = 0
    wr_by_system, wr_ids, wr_attributed = {}, [], 0
    for r in events:
        t = epoch(r.get("ts"))
        if t is None or t > at_s:
            continue
        name, pl = r.get("name"), r.get("payload") or {}
        who = position_of(r, positions, sessions)
        w = 2 ** (-(at_s - t) / 3600.0 / half_life_h)
        if name == "of.work_record":
            if lo is not None and t < lo:
                continue
            if pl.get("source") == "pr-link":   # attribution only, joined onto the merged PR above
                continue
            who = record_writer(r, positions, sessions, attrib)
            if BOT_AUTHOR.search(str(pl.get("author") or "")):
                bots += 1
                continue
            wr_ids.append(r.get("event_id"))
            wr_attributed += bool(who)
            touched = record_systems(pl, systems, pmap)
            if None in touched:
                unmapped += 1
            touched.discard(None)
            for sx in touched:
                wr_by_system.setdefault(sx, []).append(r.get("event_id"))
            for s in touched:
                if who:
                    cell(who, s)["writes"] += 1
            k = len(touched)
            for a in touched:
                for b in touched:
                    if a < b:
                        e = dsm.setdefault((a, b), {"declared": None, "interface": None, "observed_by": None,
                                                    "coupling": 0, "coupling_k": {}})
                        e["coupling_k"][k] = e["coupling_k"].get(k, 0) + 1   # PRs by how many systems they touched
                        if k <= max_systems:
                            e["coupling"] += 1
        elif name in ASK_KINDS and who:
            to = positions[who]["accountable"]
            e = pdsm.setdefault((who, to), {"asks": 0, "messages": 0, "reads": 0, "weight": 0.0})
            e["asks"] += 1
            e["weight"] += w
        elif name in PEER_KINDS:
            if name == "of.agent_message_out":
                src, dst = who, pl.get("to_position")
            else:
                src, dst = pl.get("from_position") or pl.get("from"), who
            if src in positions and dst in positions and src != dst:
                e = pdsm.setdefault((src, dst), {"asks": 0, "messages": 0, "reads": 0, "weight": 0.0})
                e["messages"] += 1
                e["weight"] += w
        elif name == "of.mandate_check" and who:
            if lo is not None and t < lo:
                continue
            if router_version(pl) < MIN_ROUTER:
                continue   # rt-v1 (no version) judged worktree paths and a shell's cwd unowned by mistake
            v, sysid = str(pl.get("verdict") or ""), pl.get("system")
            if v == "unowned" and (systems.get(sysid) or {}).get("lifecycle", "live") != "live":
                v = "non_live:" + systems[sysid]["lifecycle"]   # rt-v5 says so itself; before it, a non-live system was 'unowned'
            mc = mandate.setdefault(who, {"in_mandate": 0, "owned_by": {}, "unowned": 0, "unscoped": 0, "critical": 0,
                                          "non_live": 0, "reads": 0, "edits": 0})
            read = pl.get("tool_name") in READ_TOOLS
            mc["reads" if read else "edits"] += 1
            if v.startswith("owned_by:"):
                k = v.split(":", 1)[1]
                mc["owned_by"][k] = mc["owned_by"].get(k, 0) + 1
            elif v in ("in_mandate", "unowned", "unscoped", "critical"):
                mc[v] += 1   # unscoped: a shell at a repository root names no system; not a finding
            elif v.startswith("non_live:"):
                mc["non_live"] += 1   # work on an unsupported or retired system: shown, not a finding (registry.md)
            if v == "unowned" and router_version(pl) >= MIN_ROUTER_UNOWNED:
                area = sysid or area_of(pl.get("path"), repos=map_repos)
                orphan.setdefault(area, {})
                orphan[area][who] = orphan[area].get(who, 0) + 1
            if sysid in systems:
                c = cell(who, sysid)
                c["touches" if read else "edits"] += 1
                k = systems[sysid].get("keeper")
                if read and k and k != who and k in positions:
                    e = pdsm.setdefault((who, k), {"asks": 0, "messages": 0, "reads": 0, "weight": 0.0})
                    e["reads"] += 1
                    e["weight"] += w
        elif name == "of.artifact_read" and who:
            s = pmap.system(pl.get("repo"), pl.get("path"))
            if s:
                cell(who, s)["touches"] += 1
                k = systems.get(s, {}).get("keeper")
                if k and k != who:
                    e = pdsm.setdefault((who, k), {"asks": 0, "messages": 0, "reads": 0, "weight": 0.0})
                    e["reads"] += 1
                    e["weight"] += w

    units = {}
    for n, p in positions.items():
        units.setdefault(p["accountable"], []).append(n)
    declared_units = org_units(doc)
    return {
        "model_version": MODEL_VERSION, "org": org or doc.get("organization"), "at": iso(at_s), "registry_rev": rev,
        "max_systems": max_systems,
        "positions": positions, "systems": systems, "units": {k: sorted(v) for k, v in units.items()},
        "org_units": declared_units,
        "system_dsm": dsm, "dmm": dmm, "position_dsm": pdsm, "coverage": coverage(positions, systems, dmm, orphan),
        "unmapped_work_records": unmapped, "bot_work_records": bots,
        "mandate": mandate, "orphan_work": orphan,
        "work_records": {"total": len(wr_ids), "attributed": wr_attributed, "unmapped": unmapped, "by_system": wr_by_system},
        "loops": loops_of(doc, events, at_s),
        "cells": cells_of(positions, systems, methods_of(doc)),   # RFC 0007 §2.3
    }


LOOP_KINDS = ("of.loop_hypothesis", "of.loop_run", "of.loop_decision", "of.loop_cost")


def loops_of(doc, events, at_s):
    """{work: the loop projection} for every registry work with a `loop:` or with loop events (RFC 0005; bin/of_loops.py),
    computed once for the loop card and the loop rules (docs/diagnose.md §4.1)."""
    works = doc.get("works") if isinstance(doc.get("works"), dict) else {}
    rows = [r for r in events if (epoch(r.get("ts")) or 0) <= at_s]
    ids = {w for w, x in works.items() if isinstance(x, dict) and isinstance(x.get("loop"), dict)}
    ids |= {(r.get("payload") or {}).get("work") for r in rows if r.get("name") in LOOP_KINDS} - {None}
    if not ids:
        return {}
    import of_loops
    return {w: of_loops.project(w, (works.get(w) or {}).get("loop"), rows, at_s, work=works.get(w) or {}) for w in sorted(ids)}


# ------------------------------------------------------------------ units (docs/studio.md §2.4)

def unit_of(model, owner):
    """The positions of the unit `owner` owns: its own entry, and every position whose owner chain reaches it."""
    ps = model["positions"]
    return {n for n, p in ps.items() if n == owner or p.get("accountable") == owner or owner in owner_chain(n, ps)}


def restrict(model, names):
    """The model as the owner of a unit sees it: its positions, the systems they touch, and edges among them."""
    names = set(names)
    cells = {k: v for k, v in model["dmm"].items() if k[0] in names}
    systems = {s for _, s in cells}
    out = dict(model)
    out["positions"] = {n: p for n, p in model["positions"].items() if n in names}
    out["systems"] = {s: v for s, v in model["systems"].items() if s in systems}
    out["dmm"] = cells
    out["system_dsm"] = {k: v for k, v in model["system_dsm"].items() if k[0] in systems and k[1] in systems}
    out["position_dsm"] = {k: v for k, v in model["position_dsm"].items() if k[0] in names}
    out["units"] = {k: [n for n in v if n in names] for k, v in model["units"].items() if any(n in names for n in v)}
    out["loops"] = {w: x for w, x in (model.get("loops") or {}).items()
                    if set(x.get("doers") or []) & names or x.get("owner") in names}
    out["mandate"] = {k: v for k, v in (model.get("mandate") or {}).items() if k in names}
    out["orphan_work"] = {a: {p: n for p, n in v.items() if p in names} for a, v in (model.get("orphan_work") or {}).items()
                          if any(p in names for p in v)}
    out["coverage"] = coverage(out["positions"], out["systems"], cells, out["orphan_work"])
    if model.get("cells"):   # the unit's systems' cells, and the cells its positions hold, others' names masked
        c = model["cells"]

        def mask(r):
            k = sum(1 for n in r["doers"] if n not in names)
            return dict(r, doers=[n for n in r["doers"] if n in names] + ([f"+{k} outside the unit"] if k else []),
                        deciders=[n if n in names else "(outside the unit)" for n in r["deciders"]],
                        inactive=[n for n in r["inactive"] if n in names])
        kept = {(r["system"], r["method"]): mask(r) for r in c["cells"]
                if r["system"] in out["systems"] or set(r["doers"]) & names}
        pick = lambda rows: [kept[(r["system"], r["method"])] for r in rows if (r["system"], r["method"]) in kept]
        out["cells"] = dict(c, cells=pick(c["cells"]), gaps=pick(c["gaps"]), overlaps=pick(c["overlaps"]),
                            two_deciders=pick(c["two_deciders"]),
                            unknown=[u for u in c["unknown"] if u["position"] in names],
                            systems_with_needs=[s for s in c["systems_with_needs"] if s in out["systems"]],
                            positions_with_owns=[n for n in c["positions_with_owns"] if n in names])
    return out


# ------------------------------------------------------------------ coverage (docs/studio.md §3.4)

def coverage(positions, systems, dmm, orphan=None):
    rows = {}

    def kept(sid, seen=()):
        """A system with a keeper, kept outside the organization (`governed_by: external:<who>`), not live, or a
        supersystem whose every part is kept (registry.md; the OF team's map, 2026-11-07)."""
        s = systems.get(sid) or {}
        if s.get("keeper") or str(s.get("governed_by") or "").startswith("external:") or \
                (s.get("lifecycle") or "live") != "live":
            return True
        parts = [x for x, v in systems.items() if v.get("parent") == sid and x not in seen]
        return bool(parts) and all(kept(x, seen + (sid,)) for x in parts)

    for sid, s in systems.items():
        cells = {p: c for (p, ss), c in dmm.items() if ss == sid}
        writers = {p: c["writes"] for p, c in cells.items() if c["writes"]}
        keeper = s.get("keeper")
        flags = []
        if not keeper:
            if not kept(sid):
                flags.append("no_keeper")
        elif keeper not in positions:
            flags.append("keeper_unknown")
        elif positions[keeper]["status"] != "active":
            flags.append("keeper_" + positions[keeper]["status"])
        if len(writers) > 1:
            flags.append("fragmented")
        if any(p != keeper for p in writers):
            flags.append("non_keeper_writes")
        if (orphan or {}).get(sid):
            flags.append("orphan_work")   # work happens there, and no keeper answers for it
        if not cells and not any(x.get("parent") == sid for x in systems.values()):
            flags.append("orphan")  # no position touches it, and it is not a supersystem of parts that have them
        live = (s.get("lifecycle") or "live") == "live"
        if not live:   # a system that is not live: its changes are shown, not flagged (registry.md `lifecycle`)
            flags = []
        notes = []
        ext = str(s.get("governed_by") or "")
        if ext.startswith("external:"):
            notes.append(f"kept by {ext.split(':', 1)[1]} (external)")
        rows[sid] = {"keeper": keeper, "writers": writers, "notes": notes,
                     "homes": sorted(p for p, c in cells.items() if c["home"] and not c["keeper"]),
                     "readers": sorted(p for p, c in cells.items() if c["touches"]), "flags": flags}
    homeless = sorted(n for n, p in positions.items() if p["system"] not in systems)
    return {"systems": rows, "positions_without_system": homeless}


def cell_code(c):
    """One matrix cell as text: K keeper, W<n> writes, h home, r reads; '' when empty."""
    out = ""
    if c.get("keeper"):
        out += "K"
    if c.get("writes"):
        out += f"W{c['writes']}"
    if c.get("home") and not c.get("keeper"):
        out += "h"
    if c.get("edits") and not c.get("writes"):
        out += "m"
    if c.get("touches"):
        out += "r"
    return out


# ------------------------------------------------------------------ declared vs observed (Studio's Structure tab)

def coupling_of(entry, max_systems=MAX_SYSTEMS):
    """(count, weighted) for one pair: PRs touching at most `max_systems` systems, and Σ 1/(k−1) over all PRs."""
    ck = entry.get("coupling_k") or {}
    count = sum(n for k, n in ck.items() if int(k) <= max_systems) if ck else entry.get("coupling") or 0
    weighted = sum(n / (int(k) - 1) for k, n in ck.items() if int(k) > 1)
    return count, round(weighted, 2)


def overlay(model, max_systems=None):
    """One row per unordered system pair that is declared or observed:
    {a, b, ab, ba, interface_*, observed_by, coupling, weighted, wide, status}. Statuses:
      both           declared, and changed together in PRs touching at most `max_systems` systems;
      runtime_only   declared with `observed_by: runtime | both` (config references), no co-change: none expected;
      declared_only  declared, no co-change and no runtime basis in the window (not wrong by itself);
      undeclared     changed together, no edge either way: a missing edge or a path-claim problem.
    `wide` counts the PRs left out because they touched more systems (cross-system changes, e.g. migrations)."""
    n = max_systems or model.get("max_systems") or MAX_SYSTEMS
    pairs = {}
    for (a, b), v in model["system_dsm"].items():
        x, y = sorted((a, b))
        row = pairs.setdefault((x, y), {"a": x, "b": y, "ab": None, "ba": None, "interface_ab": None,
                                        "interface_ba": None, "observed_by": None, "_ck": {}})
        if v.get("declared"):
            side = "ab" if (a, b) == (x, y) else "ba"
            row[side], row["interface_" + side] = v["declared"], v.get("interface")
            if v.get("observed_by"):
                row["observed_by"] = v["observed_by"]
        for k, c in (v.get("coupling_k") or {}).items():
            row["_ck"][k] = row["_ck"].get(k, 0) + c
        if not v.get("coupling_k") and v.get("coupling"):
            row["_ck"][2] = row["_ck"].get(2, 0) + v["coupling"]
    for row in pairs.values():
        ck = row.pop("_ck")
        row["coupling"], row["weighted"] = coupling_of({"coupling_k": ck}, n)
        row["wide"] = sum(c for k, c in ck.items() if int(k) > n)
        declared = row["ab"] or row["ba"]
        runtime = row["observed_by"] in ("runtime", "both")
        row["status"] = ("both" if row["coupling"] else "runtime_only" if runtime else "declared_only") if declared \
            else "undeclared"
        if not declared and not row["coupling"]:
            row["status"] = "wide_only"   # only cross-system PRs touched both: shown on request
    return sorted(pairs.values(), key=lambda r: (-r["coupling"], -r["weighted"], r["a"], r["b"]))


# ------------------------------------------------------------------ code activity and evidence (RFC 0007 §2.2)

CODE_DAYS = 30        # the code evidence's window (RFC 0007 §2.2)
MAX_AREAS = 20        # an area co-change counts records touching at most this many areas; wider ones are `wide`


def area_key(repo, path, depth):
    """A changed file's area: its repository and its first `depth` directories ('storefront/src/checkout')."""
    dirs = [x for x in str(path or "").split("/") if x][:-1]
    return "/".join([str(repo)] + dirs[:depth])


def _pairs(keys, k_max, acc):
    keys = sorted(keys)
    k = len(keys)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            e = acc.setdefault((a, b), {"a": a, "b": b, "prs": 0, "weighted": 0.0, "wide": 0})
            if k <= k_max:
                e["prs"] += 1
            else:
                e["wide"] += 1
            e["weighted"] += 1.0 / (k - 1)


def code_activity(model, rows, a, b, max_systems=None, areas_depth=None):
    """Per-system code activity in [a, b) from merged work records (`of.work_record`) and open PRs (`of.pr_state`):
    {from, to, records, unmapped, bots, systems: {sid: {merged, open, writers {position: n}, unattributed,
    authors {login: n} (the unattributed records' authors), last}}, co_change: [{a, b, prs, weighted, wide}]}.
    `prs` counts records touching at most `max_systems` systems (the model's coupling rule); `weighted` is
    Σ 1/(k−1) over all. With `areas_depth`, also `areas` (the same per changed directory, no system map needed)
    and `area_co_change`: the input lab-systems-v0 clusters (RFC 0007 §2.4). Bots' records are left out, as in build."""
    positions, systems = model["positions"], model["systems"]
    sessions, pmap = session_index(positions), PathMap(systems)
    n_max = max_systems or model.get("max_systems") or MAX_SYSTEMS
    attrib = pr_attribution(rows, positions, sessions)
    per = {sid: {"merged": 0, "open": 0, "writers": {}, "unattributed": 0, "authors": {}, "last": None} for sid in systems}
    pairs, apairs, areas = {}, {}, {}
    records = unmapped = bots = 0
    latest_pr = {}
    for r in rows:
        t = epoch(r.get("ts"))
        if t is None or not a <= t < b:
            continue
        pl = r.get("payload") or {}
        if r.get("name") == "of.pr_state":   # an open PR: its latest state in the window counts once
            k = pr_key(pl)
            if k and (k not in latest_pr or t >= latest_pr[k][0]):
                latest_pr[k] = (t, r)
            continue
        if r.get("name") != "of.work_record" or pl.get("source") == "pr-link":
            continue
        if BOT_AUTHOR.search(str(pl.get("author") or "")):
            bots += 1
            continue
        records += 1
        who = record_writer(r, positions, sessions, attrib)
        touched = record_systems(pl, systems, pmap)
        unmapped += None in touched
        touched.discard(None)
        for sid in touched:
            x = per[sid]
            x["merged"] += 1
            if who:
                x["writers"][who] = x["writers"].get(who, 0) + 1
            else:
                x["unattributed"] += 1
                au = str(pl.get("author") or "(unknown)")
                x["authors"][au] = x["authors"].get(au, 0) + 1
            x["last"] = max(x["last"] or 0, t)
        _pairs(touched, n_max, pairs)
        if areas_depth:
            ks = {area_key(repo, path, areas_depth) for repo, path in _touched(pl) if repo and path}
            for ak in ks:
                ar = areas.setdefault(ak, {"records": 0, "writers": {}, "system": None})
                ar["records"] += 1
                if who:
                    ar["writers"][who] = ar["writers"].get(who, 0) + 1
            _pairs(ks, MAX_AREAS, apairs)
    for _, r in latest_pr.values():
        pl = r.get("payload") or {}
        if str(pl.get("state") or "open") != "open":
            continue
        for sid in record_systems(pl, systems, pmap) - {None}:
            per[sid]["open"] += 1
    for x in per.values():
        x["last"] = iso(x["last"]) if x["last"] else None
    out = {"method": "ca-v0", "from": iso(a), "to": iso(b), "max_systems": n_max, "records": records,
           "unmapped": unmapped, "bots": bots, "systems": per,
           "co_change": sorted(({**e, "weighted": round(e["weighted"], 2)} for e in pairs.values()),
                               key=lambda e: (-e["prs"], -e["weighted"], e["a"], e["b"]))}
    if areas_depth:
        for ak, ar in areas.items():
            repo, _, rest = ak.partition("/")
            ar["system"] = pmap.system(repo, rest + "/x" if rest else None)
        out["areas_depth"] = areas_depth
        out["areas"] = areas
        out["area_co_change"] = sorted(({**e, "weighted": round(e["weighted"], 2)} for e in apairs.values()),
                                       key=lambda e: (-e["prs"], -e["weighted"], e["a"], e["b"]))
    return out


def evidence(model, activity=None, ask_ids=None):
    """Each system's evidence (RFC 0007 §2.2): {sid: {name, kind, parent, lifecycle, live, runs, money, needs,
    governance [{fact, value, required, ask, state}], unconfirmed, code}}. A fact's state: confirmed (an ask id that
    names a known ask, or any ask id when `ask_ids` is None, i.e. not checked), ask_not_found, unconfirmed."""
    acts = (activity or {}).get("systems") or {}
    out = {}
    for sid, s in model["systems"].items():
        facts = governance(s)
        for f in facts:
            f["state"] = ("unconfirmed" if not f["ask"] else
                          "confirmed" if ask_ids is None or ask_found(f["ask"], ask_ids) else "ask_not_found")
        live = (s.get("lifecycle") or "live") == "live"
        out[sid] = {"name": s.get("name"), "kind": s.get("kind"), "parent": s.get("parent"),
                    "lifecycle": s.get("lifecycle") or "live", "live": live, "runs": s.get("runs"),
                    "money": s.get("money"), "needs": list(s.get("needs") or []), "governance": facts,
                    "unconfirmed": sum(1 for f in facts if f["required"] and f["state"] != "confirmed"),
                    "code": acts.get(sid)}
    return out


# ------------------------------------------------------------------ snapshot (docs/studio.md §2.3)

def snapshot(model):
    """Stable JSON: tuple keys become 'a → b' strings; no text beyond names, ids and the registry's own summaries."""
    def k(t):
        return f"{t[0]} -> {t[1]}"
    out = dict(model)
    out["system_dsm"] = {k(t): v for t, v in sorted(model["system_dsm"].items())}
    out["dmm"] = {k(t): v for t, v in sorted(model["dmm"].items())}
    out["position_dsm"] = {k(t): dict(v, weight=round(v["weight"], 4)) for t, v in sorted(model["position_dsm"].items())}
    out["positions"] = {n: {kk: vv for kk, vv in p.items() if kk != "session"} for n, p in model["positions"].items()}
    out["systems"] = {n: {kk: vv for kk, vv in x.items() if kk not in ("paths", "repos")} for n, x in model["systems"].items()}
    orphan = {}
    for area, v in (model.get("orphan_work") or {}).items():   # no paths in snapshots: unclaimed areas fold into one
        key = area if area in model["systems"] else "(paths no system claims)"
        for p, n in v.items():
            orphan.setdefault(key, {})
            orphan[key][p] = orphan[key].get(p, 0) + n
    out["orphan_work"] = orphan
    wr = model.get("work_records") or {}
    out["work_records"] = {k: v for k, v in wr.items() if k != "by_system"}
    out["work_records"]["by_system"] = {k: len(v) for k, v in (wr.get("by_system") or {}).items()}
    return json.dumps(out, sort_keys=True, indent=1, ensure_ascii=False) + "\n"


def snapshot_path(repo, model):
    stamp = model["at"][:16].replace(":", "") + "Z"
    return os.path.join(os.path.expanduser(repo), "snapshots", "model", f"{stamp}.json")


# ------------------------------------------------------------------ events from the local store

def load_events(conn, since=None, until=None, names=None):
    sql = "SELECT event_id, ts, name, session_id, position, org, cwd, source, harness_name, payload FROM event WHERE 1=1"
    args = []
    if since:
        sql += " AND ts >= ?"; args.append(since)
    if until:
        sql += " AND ts < ?"; args.append(until)
    if names:
        sql += " AND name IN (%s)" % ",".join("?" * len(names)); args += list(names)
    out = []
    for e, ts, n, s, pos, org, cwd, src, harness, pl in conn.execute(sql + " ORDER BY ts, rowid", args):
        try:
            payload = json.loads(pl) if pl else {}
        except ValueError:
            payload = {}
        out.append({"event_id": e, "ts": ts, "name": n, "session_id": s, "position": pos, "org": org,
                    "cwd": cwd, "source": src, "harness": harness, "payload": payload})
    if not names or "of.human_prompt" in names:
        # a correction may come days after its prompt: every one in the store, whatever window the caller loads
        have = {r["event_id"] for r in out if r["name"] == "of.prompt_origin"}
        for e, pl in conn.execute("SELECT event_id, payload FROM event WHERE name = 'of.prompt_origin' ORDER BY ts, rowid"):
            if e not in have:
                try:
                    out.append({"event_id": e, "name": "of.prompt_origin", "payload": json.loads(pl) if pl else {}})
                except ValueError:
                    pass
    import of_asks  # the one reader of corrected prompt origins (of_platform); it drops the correction rows
    return of_asks.apply_prompt_origins(out)


# A prompt carries the owner's words only when its origin is human or paste; scheduled, system, injected and unknown
# prompts are the harness's or a tool's (docs/events.md, "Injected and scheduled prompts"; of_asks answers the same way).
OWNER_ORIGINS = ("human", "paste")


def owner_prompt(row):
    """Is this of.human_prompt the owner's own words? Read after of_asks.apply_prompt_origins (load_events applies
    it), so the origin is the corrected one. A prompt without an origin (old imports) counts, as it always did."""
    o = (row.get("payload") or {}).get("origin")
    return o is None or o in OWNER_ORIGINS


# ------------------------------------------------------------------ the rule surface (stable)
# The detection rules (bin/of_diagnose_rules.py, of_field-analyst's) read MODEL(t) and the typed rows only through the
# names in RULE_SURFACE. A name's parameters and meaning change only after a note to of_field-analyst; tests/test_studio.py
# pins the parameters and checks that the rules import nothing else of the platform. The issue record (issue, issue_id,
# spec_part) is the contract between a rule and the run (bin/of_diagnose.py).

def in_window(row, a, b):
    t = epoch(row.get("ts"))
    return t is not None and a <= t < b


def is_received(r):
    """A received peer message as the receiving session saw it. The relay client also writes its own delivery log as
    of.agent_message_in with source `of`; counting both would double every relayed message."""
    return r.get("source") != "of"


def received_labels(r):
    """The classifications of a received peer message (bin/of_classify.py, set at derive)."""
    return (r.get("payload") or {}).get("classifications") or {}


# The management functions (docs/method.md; registry.md `mandate_functions`). Same tuple as MANAGEMENT_FUNCTIONS in
# bin/of; tests/test_studio.py asserts they stay equal.
MANAGEMENT_FUNCTIONS = ("strategy", "goals", "operations", "administration", "corporate_governance",
                        "corporate_finance", "management_accounting", "marketing", "hr", "leadership", "org_change",
                        "asset_management", "community", "productization")


def spec_part(spec, key, part, default=None):
    """A rule field that may be one value or {part: value}; an issue without a part, or a part the dict does not
    name, reads its `default` key (of_field-analyst, 2026-11-07)."""
    v = spec.get(key, default)
    if not isinstance(v, dict):
        return v
    return v.get(part) if part and part in v else v.get("default", default)


def structure_keeper(model):
    """The position that runs the loop for the organization: the keeper of its creating system."""
    for s in model["systems"].values():
        if s.get("kind") == "creating" and s.get("keeper"):
            return s["keeper"]
    return None


def issue_id(rule, affected):
    return hashlib.sha256((rule + "|" + "|".join(sorted(affected))).encode()).hexdigest()[:12]


def issue(cat, name, affected, evidence, summary, delta_m=0.0, critical=False, part=None, key=None):
    """key: what identifies the issue when `affected` (sorted) does not, e.g. a directed pair 'p->q'."""
    spec = cat["rules"][name]
    p = spec_part(spec, "p", part) or 0.5
    m = spec_part(spec, "m_decide", part) or 5
    rem = spec.get("remedies") or []
    rem = (rem.get(part) if part and part in rem else rem.get("default") or []) if isinstance(rem, dict) else rem
    sev = spec_part(spec, "severity", part) or (cat.get("defaults") or {}).get("severity") or "medium"
    return {"issue_id": issue_id(name, [key] if key else affected), "rule": f"ap.{name}", "rule_version": f"ap.{name}@{spec.get('version', 0)}",
            "part": part, "affected": sorted(affected), "summary": summary, "evidence": evidence, "severity": sev,
            "remedies": list(rem), "critical": critical, "decider": spec_part(spec, "decider", part, "owner") or "owner",
            "score": round(p * delta_m / m, 3), "score_terms": {"p": p, "delta_m": round(delta_m, 1), "m_decide": m},
            "state": "open"}


def session_positions(model, rows):
    """{session id: position}: the holder index, then any event of the session that names a registered position."""
    positions = model["positions"]
    out = dict(session_index(positions))
    for r in rows:
        if r.get("position") in positions and r.get("session_id"):
            out.setdefault(r["session_id"], r["position"])
    return out


_FROM_NAME = re.compile(r'from-name="([^"]+)"')
_FROM_ATTR = re.compile(r'<(?:cross-session|agent)-message[^>]*?\sfrom="([^"]+)"')


_WRAPPED = re.compile(r"^.*?<(?:cross-session|agent)-message[^>]*>", re.S)


def _prefix(text, n=60):
    return " ".join(str(text or "").split())[:n]


def message_senders(model, rows, a, b, slack_s=5.0):
    """[(event, sender position | None, from-name, how)] for every received message in the window. The sender is
    resolved (of_field-analyst, 2026-11-06): (1) the matching of.agent_message_out — the same text prefix within
    ±slack_s — whose session names a position; (2) the wrapper's from-name through registry names and aliases."""
    positions = model["positions"]
    sp = session_positions(model, rows)
    names = {str(x).lower(): n for n, pos in positions.items() for x in [n] + list(pos.get("aliases") or [])}
    outs = {}
    for r in rows:
        if r["name"] == "of.agent_message_out":
            k = _prefix((r.get("payload") or {}).get("text"))
            if k:
                outs.setdefault(k, []).append((epoch(r.get("ts")) or 0, r))
    res = []
    for r in rows:
        if r["name"] != "of.agent_message_in" or not in_window(r, a, b) or not is_received(r):
            continue
        pl = r.get("payload") or {}
        text = str(pl.get("text") or "")
        m = _FROM_NAME.search(text)
        ma = _FROM_ATTR.search(text)
        fname = m.group(1) if m else str(pl.get("from") or (ma.group(1) if ma else ""))
        t, p, how = epoch(r.get("ts")) or 0, None, None
        cands = [(abs(t - to), o) for to, o in outs.get(_prefix(_WRAPPED.sub("", text, count=1)), ()) if abs(t - to) <= slack_s]
        if cands:
            o = min(cands, key=lambda x: x[0])[1]
            p = o.get("position") if o.get("position") in positions else sp.get(o.get("session_id"))
            how = "out" if p else None
        if not p:
            p = names.get(fname.lower()) or names.get(str(pl.get("from_position") or "").lower())
            how = "from-name" if p else None
        res.append((r, p, fname, how))
    return res


_SUBAGENT = re.compile(r"^a[0-9a-f]{16}$")


def unresolved_senders(model, rows, a, b):
    """Counter{from-name}: received messages whose sender resolves to no position (for the structure keeper:
    a missing alias, or a session without a position). In-process subagents are never positions and are left out:
    `from_kind: subagent` when the derive marks it, else a from-name of `a` + 16 hex."""
    out = collections.Counter()
    for r, p, f, _ in message_senders(model, rows, a, b):
        if p or (r.get("payload") or {}).get("from_kind") == "subagent" or _SUBAGENT.match(f or ""):
            continue
        out[f or "(none)"] += 1
    return out


# readings of the measures (bin/of_measures.py, which imports this module: imported when called)

def founder_messages(rows):
    """The owner's own messages among rows, at the pinned baseline (of_measures.founder_messages)."""
    import of_measures
    return of_measures.founder_messages(rows)


def message_categories(row):
    """The plumbing categories of an owner message at the pinned baseline (routing, relay_paste, status_poll, ...)."""
    import of_measures
    return of_measures._cats(row, of_measures.FA)


def asks_of(model, rows, who=HUMAN):
    """The asks to the owner `who` with their lifecycle (of_measures.asks_of)."""
    import of_measures
    return of_measures.asks_of(model, rows, who)


def little(asks, a, b):
    """L, lambda and W of asks over [a, b) with the Little residual (of_measures.little)."""
    import of_measures
    return of_measures.little(asks, a, b)


def owner_measures(model, rows, a, b, who=HUMAN):
    """The owner's constraint measures over [a, b) (of_measures.owner): plumbing_share, inbound_concentration,
    owner_messages, status_polls, ..."""
    import of_measures
    return of_measures.owner(model, rows, a, b, who=who)


def inbound_queues(model, rows, a, b, old_after_s=None):
    """{position: queue episodes} from of.inbound_queue over [a, b) (of_measures.inbound_queues)."""
    import of_measures
    kw = {} if old_after_s is None else {"old_after_s": old_after_s}
    return of_measures.inbound_queues(model, rows, a, b, **kw)


def loops(model):
    """{work: the loop projection} (docs/diagnose.md §4.1): fields from the registry and the loop events, and
    `metrics` (rungs, wip, production, babysit) derived once here for the loop card and the loop rules."""
    return model.get("loops") or {}


RULE_SURFACE = ("HUMAN", "BOT_AUTHOR", "MANAGEMENT_FUNCTIONS", "epoch", "iso", "in_window", "is_received",
                "received_labels", "owner_prompt", "session_index", "position_of", "session_positions",
                "message_senders", "structure_keeper", "overlay", "pr_key", "pr_attribution",
                "founder_messages", "message_categories", "asks_of", "little", "owner_measures", "inbound_queues",
                "loops", "issue", "issue_id", "spec_part")
