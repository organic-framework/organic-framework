"""`of access map`: who reaches what, through what, with which limit, approved how, and when each entry was last used
(docs/access.md §2). Owned by of_server.

This is step 1 of docs/access.md §6 (hand-over 64ece894 from of_architect): the map stands on what exists today, the
credentials catalogue (docs/registry.md, Credentials) and the grants (docs/registry.md, Grants) of every organization
listed in orgs.yaml. Keeper lists (registry/access/<keeper>.yaml) and their `of.access_used` records come with steps 2
and 4. Names, paths and references only: it never opens a credential file and never reads a value.

- A credential is reached by each position that lists it and by its keeper, which keeps it (docs/registry.md).
- "Approved" is the organization repository's commit that last changed the line giving that reach (the position's
  listing, or the credential's `keeper:`), with its `Position:` trailer: for the catalogue the structure keeper's
  commit is the record (docs/access.md §3). A line not committed yet reads "uncommitted".
- "Last used" comes from the secrets broker's records (`secret_uses` in the server's store) on a machine that runs the
  server, for credentials it serves by their `ref`: "none here" when that broker never served it, "no broker here" on
  a machine without one. A use of a file is not recorded today; it reads "not recorded".
- A grant shows its tier, scope, expiry, whether its signature verifies against this machine's pinned grant key, and
  its source.
"""
import datetime
import json
import os
import sqlite3
import subprocess
import time

UNCOMMITTED = "0" * 40
STORES = {"op": "1Password", "keychain": "keychain", "envfile": "env file"}


class AccessError(Exception):
    pass


# ---------------------------------------------------------------- reading

def _orgs(orgs_file=None):
    """[(org, repository root)] for the organizations with a local checkout, in orgs.yaml's order."""
    import of_credentials
    return [(org, os.path.dirname(os.path.dirname(path))) for org, path in of_credentials._registries(orgs_file)]


def _read(path):
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def _lines(text):
    """{("listing", position, credential) | ("keeper", credential) | ("credential", credential): line (1-based)},
    from the YAML's nodes, so a flow list and a block list read alike."""
    import yaml
    try:
        root = yaml.compose(text)
    except yaml.YAMLError:
        return {}
    out = {}
    if not isinstance(root, yaml.MappingNode):
        return out

    def scalar(n):
        return n.value if isinstance(n, yaml.ScalarNode) else None

    for k, v in root.value:
        if scalar(k) == "credentials" and isinstance(v, yaml.MappingNode):
            for ck, cv in v.value:
                cid = scalar(ck)
                if cid is None:
                    continue
                out[("credential", cid)] = ck.start_mark.line + 1
                if isinstance(cv, yaml.MappingNode):
                    for fk, _ in cv.value:
                        if scalar(fk) == "keeper":
                            out[("keeper", cid)] = fk.start_mark.line + 1
        if scalar(k) == "positions" and isinstance(v, yaml.SequenceNode):
            for pn in v.value:
                if not isinstance(pn, yaml.MappingNode):
                    continue
                fields = {scalar(fk): fv for fk, fv in pn.value}
                name, creds = scalar(fields.get("name")) if fields.get("name") else None, fields.get("credentials")
                if name and isinstance(creds, yaml.SequenceNode):
                    for item in creds.value:
                        if scalar(item) is not None:
                            out[("listing", name, scalar(item))] = item.start_mark.line + 1
    return out


def _git(root, args, run=subprocess.run):
    try:
        r = run(["git", "-c", "core.fsmonitor=false", "-C", root, *args], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _blame(root, rel, run=subprocess.run):
    """{line: commit} for one file of a repository; an uncommitted line's commit is UNCOMMITTED; {} when git cannot
    tell (no repository, the file not tracked)."""
    out = {}
    for ln in (_git(root, ["blame", "--porcelain", "--", rel], run) or "").splitlines():
        parts = ln.split(" ")
        if len(parts) >= 3 and len(parts[0]) == 40 and all(ch in "0123456789abcdef" for ch in parts[0]):
            try:
                out[int(parts[2])] = parts[0]
            except ValueError:
                pass
    return out


def _commits(root, shas, run=subprocess.run):
    """{commit: (short id, date, position from its `Position:` trailer or None)}."""
    shas = sorted({s for s in shas if s and s != UNCOMMITTED})
    if not shas:
        return {}
    fmt = "%H%x1f%h%x1f%ad%x1f%(trailers:key=Position,valueonly,separator=%x2c)%x1e"
    out = {}
    for rec in (_git(root, ["log", "--no-walk=unsorted", "--date=short", f"--format={fmt}", *shas], run) or "").split("\x1e"):
        parts = rec.strip("\n").split("\x1f")
        if len(parts) == 4:
            out[parts[0]] = (parts[1], parts[2], parts[3].strip() or None)
    return out


def _approved(commit, info):
    if commit is None:
        return "not in git"
    if commit == UNCOMMITTED:
        return "uncommitted"
    short, date, position = info.get(commit) or (commit[:9], "?", None)
    return f"{short} {date}" + (f" {position}" if position else "")


def _state_dir(server_yaml=None):
    """The server's state folder, as of_server.load_config reads it (server.yaml's state_dir, else its default)."""
    import yaml
    path = server_yaml or os.path.expanduser("~/.config/of/server.yaml")
    try:
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        cfg = {}
    return os.path.expanduser(str(cfg.get("state_dir") or "~/.local/state/of/server"))


def broker_uses(state_dir=None):
    """{(org, position, credential): the last allowed use, epoch seconds} from the broker's records (server.db
    `secret_uses`, docs/agent-secrets.md §5.3) on a machine that runs the server; None where no server runs, so
    nothing was observed here. Read-only."""
    path = os.path.join(state_dir or _state_dir(), "server.db")
    if not os.path.exists(path):
        return None
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return {}
    try:
        rows = c.execute("SELECT org, position, credential, max(at) FROM secret_uses WHERE decision = 'allow'"
                         " GROUP BY org, position, credential").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        c.close()
    return {(o, p, cr): at for o, p, cr, at in rows if at is not None}


def _pinned_keys():
    """What this machine trusts for grants: its pinned grant public key (as `of registry check` reads it)."""
    import of_grants
    try:
        with open(of_grants.pin_path()) as fh:
            return of_grants.Keys(ed25519=[ln for ln in fh.read().splitlines() if ln.strip()])
    except OSError:
        return of_grants.Keys()


# ---------------------------------------------------------------- the map

def _through(c):
    ref = c.get("ref") if isinstance(c.get("ref"), str) else None
    paths = [p for p in c.get("paths") or [] if isinstance(p, str)] if isinstance(c.get("paths"), list) else []
    chain = [k for k in c.get("keychain") or [] if isinstance(k, str)] if isinstance(c.get("keychain"), list) else []
    parts = []
    if ref:
        scheme = ref.split("://", 1)[0]
        parts.append(f"{STORES.get(scheme, scheme)} via the broker")
    if paths:
        parts.append("file" if len(paths) == 1 else f"{len(paths)} files")
    if chain:
        parts.append("keychain, the owner's programs only")
    return ", ".join(parts) or "nothing named"


def _iso(epoch):
    return datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def _when(v):
    """A time as the registry wrote it; YAML reads an unquoted one as a datetime, shown back in UTC with a Z."""
    if isinstance(v, datetime.datetime):
        v = v if v.tzinfo else v.replace(tzinfo=datetime.timezone.utc)
        return v.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(v)


def _scope_text(scope):
    parts = []
    for k in sorted(scope or {}):
        v = scope[k]
        if v in (None, [], "", {}):
            continue
        vals = v if isinstance(v, list) else [v]
        shown = ", ".join(str(x) for x in vals[:3]) + (f" and {len(vals) - 3} more" if len(vals) > 3 else "")
        parts.append(f"{k}: {shown}")
    return "; ".join(parts) or "unrestricted"


def org_map(org, root, grants, keys, now_iso, uses, run=subprocess.run):
    """The map of one organization: {org, rows: [credential reach], grants: [grant rows], unreached: [ids],
    problems: [text]}."""
    import yaml
    rel = os.path.join("registry", "positions.yaml")
    text = _read(os.path.join(root, rel))
    if text is None:
        raise AccessError(f"{org}: cannot read {os.path.join(root, rel)}")
    try:
        doc = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise AccessError(f"{org}: {rel} is not valid YAML ({str(e).splitlines()[0]})") from None
    creds = doc.get("credentials") if isinstance(doc.get("credentials"), dict) else {}
    positions = [p for p in doc.get("positions") or [] if isinstance(p, dict) and p.get("name")]
    lines = _lines(text)
    blame = _blame(root, rel, run)
    reach = []  # (position, credential, how, line)
    for p in positions:
        for cid in p.get("credentials") or [] if isinstance(p.get("credentials"), list) else []:
            reach.append((str(p["name"]), str(cid), "lists", lines.get(("listing", str(p["name"]), str(cid)))))
    for cid, c in creds.items():
        keeper = c.get("keeper") if isinstance(c, dict) else None
        if keeper:
            reach.append((str(keeper), str(cid), "keeps", lines.get(("keeper", str(cid)))))
    commits = {ln: blame.get(ln) for _, _, _, ln in reach if ln}
    info = _commits(root, commits.values(), run)
    names = {str(p["name"]) for p in positions}
    rows, problems, seen = [], [], set()
    for position, cid, how, ln in reach:
        if (position, cid) in seen:
            continue  # a keeper that also lists it: one row, as it lists it
        seen.add((position, cid))
        c = creds.get(cid)
        if not isinstance(c, dict):
            problems.append(f"{position} lists {cid}, which the catalogue does not define")
            c = {}
        if position not in names:
            problems.append(f"{cid}'s keeper {position} is not a position of {org}")
        at = (uses or {}).get((org, position, cid))
        if at:
            last = _iso(at)
        elif not c.get("ref"):
            last = "not recorded"  # a file's use is not recorded today (docs/access.md §5 brings of.access_used)
        else:
            last = "none here" if uses is not None else "no broker here"
        rows.append({"org": org, "position": position, "access": cid, "how": how, "through": _through(c),
                     "custody": c.get("custody") is True, "limit": None,
                     "approved": _approved(commits.get(ln) if blame else None, info),
                     "last_used": last,
                     "acts_as": c.get("acts_as"),
                     "paths": [p for p in c.get("paths") or [] if isinstance(p, str)]
                     if isinstance(c.get("paths"), list) else [],
                     "ref": c.get("ref") if isinstance(c.get("ref"), str) else None})
    reached = {cid for _, cid, _, _ in reach}
    unreached = sorted(str(cid) for cid in creds if str(cid) not in reached)
    import of_grants
    grows = []
    for g in grants:
        if not isinstance(g, dict):
            continue
        ok, why = of_grants.valid(g, keys, now_iso)
        limit = []
        if g.get("expires"):
            limit.append(f"until {_when(g['expires'])}")
        if g.get("counter") is not None:
            limit.append(f"{g['counter']} uses")
        prov = g.get("provenance") if isinstance(g.get("provenance"), dict) else {}
        grows.append({"org": org, "position": str(g.get("subject") or "?"), "grant": str(g.get("id") or "?"),
                      "tier": g.get("tier"), "effect": g.get("effect") or "allow",
                      "scope": _scope_text(g.get("scope") if isinstance(g.get("scope"), dict) else {}),
                      "limit": ", ".join(limit) or None, "state": "in force" if ok else why,
                      "approved": f"issued by {g.get('issuer') or '?'}"
                                  + (f", {prov['source']}" if prov.get("source") else ""),
                      "note": g.get("note")})
    rows.sort(key=lambda r: (r["position"], r["access"]))
    grows.sort(key=lambda r: (r["position"], r["grant"]))
    return {"org": org, "rows": rows, "grants": grows, "unreached": unreached, "problems": problems}


def build(org=None, position=None, orgs_file=None, uses=None, keys=None, now_iso=None, run=subprocess.run):
    """[org_map] for every organization with a local checkout, or the one named; rows narrowed to one position."""
    import of_grants
    known = _orgs(orgs_file)
    if org and org not in {o for o, _ in known}:
        raise AccessError(f"no organization {org!r} with a local checkout in orgs.yaml (known: "
                          f"{', '.join(o for o, _ in known) or 'none'})")
    of_file = orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
    try:
        paths = of_grants._org_grants_paths(of_file)
    except OSError:
        paths = {}
    uses = broker_uses() if uses is None else (uses or None)
    keys = _pinned_keys() if keys is None else keys
    now_iso = now_iso or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    out = []
    for o, root in known:
        if org and o != org:
            continue
        grants = []
        gp = paths.get(o)
        if gp and os.path.exists(gp):
            import yaml
            try:
                with open(gp) as fh:
                    grants = (yaml.safe_load(fh) or {}).get("grants") or []
            except (OSError, yaml.YAMLError) as e:
                raise AccessError(f"{o}: cannot read its grants ({str(e).splitlines()[0]})") from None
        m = org_map(o, root, grants, keys, now_iso, uses, run)
        if position:
            m["rows"] = [r for r in m["rows"] if r["position"] == position]
            m["grants"] = [g for g in m["grants"] if g["position"] in (position, "*")]
        out.append(m)
    return out


# ---------------------------------------------------------------- printing

def _table(header, rows, widths):
    def cut(s, w):
        s = "" if s is None else str(s)
        return s if len(s) <= w else s[:w - 1] + "…"
    cols = [max(len(h), *(len(cut(r[i], widths[i])) for r in rows)) if rows else len(h) for i, h in enumerate(header)]
    lines = ["  ".join(h.ljust(cols[i]) for i, h in enumerate(header)).rstrip()]
    for r in rows:
        lines.append("  ".join(cut(r[i], widths[i]).ljust(cols[i]) for i in range(len(header))).rstrip())
    return lines


def render(maps, long=False):
    out = []
    for m in maps:
        n_pos = len({r["position"] for r in m["rows"]})
        out.append(f"{m['org']}: {len(m['rows'])} credential reaches by {n_pos} positions, {len(m['grants'])} grants"
                   " (from the catalogue and grants; keeper lists come with docs/access.md §6 step 2)")
        if m["rows"]:
            body = [(r["position"], r["access"], r["how"] + (", custody" if r["custody"] else ""), r["through"],
                     r["limit"] or "-", r["approved"], r["last_used"]) for r in m["rows"]]
            out += ["  " + ln for ln in _table(("POSITION", "REACHES", "HOW", "THROUGH", "LIMIT", "APPROVED",
                                                "LAST USED"), body, (32, 36, 14, 40, 20, 36, 18))]
            if long:
                for r in m["rows"]:
                    where = r["ref"] or ", ".join(r["paths"]) or "-"
                    out.append(f"    {r['position']} / {r['access']}: {r['acts_as'] or 'acts as: not stated'}; {where}")
        if m["grants"]:
            body = [(g["position"], g["grant"], g["tier"], g["effect"], g["scope"], g["limit"] or "-", g["state"],
                     g["approved"]) for g in m["grants"]]
            out += ["  " + ln for ln in _table(("SUBJECT", "GRANT", "TIER", "EFFECT", "SCOPE", "LIMIT", "STATE",
                                                "APPROVED"), body, (32, 24, 18, 6, 60, 26, 26, 40))]
        if m["unreached"]:
            out.append(f"  reach no position ({len(m['unreached'])}): {', '.join(m['unreached'])}")
        for p in m["problems"]:
            out.append(f"  problem: {p}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def cli(args, out=print):
    if args.action != "map":
        raise AccessError(f"unknown action {args.action!r}")
    maps = build(org=args.org, position=args.position)
    if args.json:
        out(json.dumps(maps, indent=1, ensure_ascii=False))
    else:
        out(render(maps, long=getattr(args, "long", False)).rstrip("\n"))
    return 0
