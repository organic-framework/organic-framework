"""The credentials catalogue (docs/registry.md, Credentials; docs/guard.md G14).

of_architect's decision (8ef7bc0, 2026-11-11, from a user organization): every agent runs as one OS user, so a
file's mode keeps no position from another's credentials, and the guard's name patterns missed files. Each
organization's registry lists its credentials (id: paths, acts_as, keeper) and each position the ids it uses.

- Under the organization's credential roots (`credential_roots:`, default `~/.config/<organization>/`), a position may
  read or use a file only when a credential it lists, or keeps, names it. A file there that no credential names is
  denied to all, and reported to the structure keeper as undeclared.
- A path outside every root is protected only where a credential names it: then only its positions and keeper.
- A credential is listed only by positions of its own organization; another organization's root is denied the same way.

The catalogue holds names and paths only, never a value. This module loads and checks it (`of registry check`),
caches it for the guard hook (one JSON read per call), decides a path, and replays recent sessions to list the files
each organization's positions read under its root (`report`), so its structure keeper can fill the catalogue.
"""

import collections
import datetime
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
CACHE = "guard-credentials.json"
# warn: allowed, noted once per session and recorded; deny: denied. Two switches (a user organization's plan, msg
# 01a1c89d, 2026-11-11): a file a credential names, read by a position that neither lists nor keeps it (MODE_NAMED;
# phase 1, a catalogue of explicit paths and no root), and an undeclared file under a root (MODE_ROOT; phase 2, once
# the working files have moved out). Each goes to deny after a replay of the catalogue as filled.
# MODE_NAMED denies from 2026-11-11 (msg 01a1c8d2): the replay of a user organization's phase 1 catalogue over 21
# days found 81 denies of 465 calls, all intended (k8s_keeper's use of a hosting provider's API goes through a
# reviewed script, the rest were one-offs). An organization without a catalogue is not touched.
MODE_NAMED = "deny"
MODE_ROOT = "warn"


def mode(ids):
    """The switch for one hit: a named credential (ids) or an undeclared file under a root (no ids)."""
    return MODE_NAMED if ids else MODE_ROOT
NOT_A_ROOT = {os.path.expanduser("~/.config/of")}  # the platform's own config, under the G1 name rules only


def _orgs_file(orgs_file=None):
    return orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")


def _registries(orgs_file=None):
    """[(org name, registry path)] for the organizations with a local checkout."""
    import yaml
    try:
        with open(_orgs_file(orgs_file)) as fh:
            doc = yaml.safe_load(fh) or {}
    except OSError:
        return []
    base = os.path.dirname(_orgs_file(orgs_file))
    out = []
    for o in doc.get("orgs") or []:
        if isinstance(o, dict) and o.get("path"):
            root = os.path.join(base, os.path.expanduser(str(o["path"])))
            out.append((o.get("name") or os.path.basename(root), os.path.join(root, "registry", "positions.yaml")))
    return out


def _abs(p):
    p = re.sub(r"^\$\{?HOME\}?(?=/|$)", "~", str(p).strip())
    return os.path.normpath(os.path.expanduser(p))


def _org_entry(org, doc):
    organization = str(doc.get("organization") or org)
    given = doc.get("credential_roots") if isinstance(doc.get("credential_roots"), list) else None
    roots = [_abs(r) for r in (given or [f"~/.config/{organization}"]) if isinstance(r, str)]
    creds = {}
    for cid, c in (doc.get("credentials") or {}).items() if isinstance(doc.get("credentials"), dict) else ():
        c = c if isinstance(c, dict) else {}
        paths = c.get("paths") if isinstance(c.get("paths"), list) else []
        creds[str(cid)] = {"paths": [_abs(p) for p in paths if isinstance(p, str)], "keeper": c.get("keeper"),
                           "acts_as": c.get("acts_as"), "ref": c.get("ref") if isinstance(c.get("ref"), str) else None,
                           "rotate_days": c.get("rotate_days") if isinstance(c.get("rotate_days"), int) else 90}
    positions = {}
    for p in doc.get("positions") or []:
        if isinstance(p, dict) and p.get("name"):
            ids = p.get("credentials") if isinstance(p.get("credentials"), list) else []
            positions[str(p["name"])] = [str(i) for i in ids]
    contacts = sorted({str(c).strip().lower() for a in doc.get("authorities") or [] if isinstance(a, dict)
                       for c in a.get("contacts") or [] if isinstance(c, str) and c.strip()})
    return {"roots": [r for r in roots if r not in NOT_A_ROOT], "credentials": creds, "positions": positions,
            "authorities": contacts}  # docs/guard.md G16: the organization's own regulators, hosts and addresses


def load(orgs_file=None):
    """{org: {roots, credentials: {id: {paths, keeper, acts_as}}, positions: {name: [ids]}}} from every registry."""
    import yaml
    out = {}
    for org, path in _registries(orgs_file):
        try:
            with open(path) as fh:
                doc = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        out[org] = _org_entry(org, doc)
    return out


def cached(state_dir, orgs_file=None):
    """The catalogue for the guard hook: rebuilt only when orgs.yaml or a registry changed (by mtime), so a hook call
    reads one JSON file and runs no YAML in the common case."""
    def mt(p):
        try:
            return os.stat(p).st_mtime_ns
        except OSError:
            return None
    path = os.path.join(state_dir, CACHE)
    try:
        with open(path) as fh:
            c = json.load(fh)
    except (OSError, ValueError):
        c = {}
    stamp = [mt(_orgs_file(orgs_file))]
    regs = c.get("registries") or []
    stamp += [mt(r) for r in regs]
    if c.get("stamp") == stamp and isinstance(c.get("orgs"), dict):
        return c["orgs"]
    regs = [r for _, r in _registries(orgs_file)]
    orgs = load(orgs_file)
    c = {"stamp": [mt(_orgs_file(orgs_file))] + [mt(r) for r in regs], "registries": regs, "orgs": orgs}
    try:
        os.makedirs(state_dir, exist_ok=True)
        tmp = f"{path}.{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(c, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    return orgs


def _glob_rx(pattern):
    """A path glob: ** any depth, * and ? within one name, [...] a class."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        elif pattern[i] == "[" and "]" in pattern[i + 1:]:
            j = pattern.index("]", i + 1)
            out, i = out + "[" + re.sub(r"([\\\]])", r"\\\1", pattern[i + 1:j]) + "]", j + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return out


def _match(pattern, path):
    """A credential's path is a file, or a glob (`dir/**` for every file under a directory)."""
    if not any(ch in pattern for ch in "*?["):
        return path == pattern
    return re.fullmatch(_glob_rx(pattern), path) is not None


def _under(roots, path):
    return any(r and (path == r or path.startswith(r.rstrip("/") + "/")) for r in roots)


def interesting(cat):
    """The guard's predicate: a path under some root, or one a credential names."""
    roots = [r for o in cat.values() for r in o.get("roots") or []]
    pats = [p for o in cat.values() for c in o["credentials"].values() for p in c["paths"]]

    def keep(path):
        return _under(roots, path) or any(_match(p, path) for p in pats)
    return keep


def decide(path, position, org, cat):
    """(allowed, owner org, [credential ids naming it]) for one file; None when the catalogue says nothing about it."""
    for o, ent in cat.items():
        ids = [cid for cid, c in ent["credentials"].items() if any(_match(p, path) for p in c["paths"])]
        if not ids and not _under(ent.get("roots") or [], path):
            continue
        mine = set(ent["positions"].get(position) or []) if org == o else set()
        keeps = {cid for cid in ids if ent["credentials"][cid].get("keeper") == position} if org == o else set()
        return bool(set(ids) & mine or keeps), o, ids
    return None


def position_view(position, org, cat):
    """What one position may and may not touch, for `of spawn` to write the session's own deny rules (option 2 of
    the decision; of_platform's): {roots: every organization's root, allowed: the paths of the credentials it lists
    or keeps, denied: the paths of every other credential}. Claude Code's deny rules beat its allow rules, so a
    root cannot be denied with its allowed files carved out natively; the guard (G14) does that part."""
    allowed, denied = [], []
    for o, ent in cat.items():
        mine = set(ent["positions"].get(position) or []) if o == org else set()
        for cid, c in ent["credentials"].items():
            ok = o == org and (cid in mine or c.get("keeper") == position)
            (allowed if ok else denied).extend(c["paths"])
    return {"roots": sorted(r for e in cat.values() for r in e.get("roots") or []), "allowed": sorted(set(allowed)),
            "denied": sorted(set(denied) - set(allowed))}


def reason(path, position, owner, ids, cat):
    """The words the agent and the record get."""
    name = path.replace(os.path.expanduser("~"), "~", 1)
    if not ids:
        return (f"{name} is under {owner}'s credential root and no credential in its registry names it (an undeclared "
                f"credential); its structure keeper adds it to `credentials:` and lists it for the positions that "
                f"use it, or the file moves out of the root (docs/registry.md, Credentials)")
    keepers = sorted({str(cat[owner]['credentials'][i].get('keeper')) for i in ids})
    return (f"{name} is credential {', '.join(ids)} of {owner}, which position {position or '(none)'} does not list; "
            f"its keeper ({', '.join(keepers)}) or the structure keeper lists it for the position, or a reviewed "
            f"script that uses it runs instead (docs/registry.md, Credentials)")


_CONTACT_RE = re.compile(r"^(?:[\w.+-]+@)?(?:[a-z0-9-]+\.)+[a-z]{2,}$", re.I)


def authorities_problems(rel, entries):
    """docs/registry.md, Authorities: a list of {name, contacts: [e-mail addresses or hosts], evidence}."""
    if entries is None:
        return []
    if not isinstance(entries, list):
        return [f"{rel}: authorities must be a list of {{name, contacts, evidence}}"]
    out = []
    for k, a in enumerate(entries):
        where = f"{rel}: authorities[{k}]"
        if not isinstance(a, dict) or not isinstance(a.get("name"), str) or not a["name"].strip():
            out.append(f"{where}: needs a name")
            continue
        where = f"{rel}: authority {a['name']}"
        contacts = a.get("contacts")
        if not isinstance(contacts, list) or not contacts:
            out.append(f"{where}: contacts must be a non-empty list of e-mail addresses or hosts")
        for c in contacts if isinstance(contacts, list) else []:
            if not isinstance(c, str) or not _CONTACT_RE.match(c.strip()):
                out.append(f"{where}: {str(c)[:40]!r} is not an e-mail address or a host")
        if not isinstance(a.get("evidence"), str) or not a["evidence"].strip():
            out.append(f"{where}: evidence must say what makes it the organization's authority (a licence, a record)")
    return out


def check(orgs_file=None):
    """Problems in the catalogues, as `of registry check` prints them."""
    import yaml
    problems, claimed, refs = [], {}, {}
    for org, path in _registries(orgs_file):
        rel = f"{org}:registry/positions.yaml"
        try:
            with open(path) as fh:
                doc = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue  # load_positions reports a missing or broken registry
        names = {str(p.get("name")) for p in doc.get("positions") or [] if isinstance(p, dict)}
        roots = doc.get("credential_roots")
        home = os.path.expanduser("~").rstrip("/") + "/"
        if roots is not None and not isinstance(roots, list):
            problems.append(f"{rel}: credential_roots must be a list of directories under the home directory")
        for r in roots if isinstance(roots, list) else []:
            if not isinstance(r, str) or not (_abs(r) + "/").startswith(home) or _abs(r) + "/" == home:
                problems.append(f"{rel}: credential_roots: {r!r} is not a directory under the home directory")
            elif _abs(r) in NOT_A_ROOT:
                problems.append(f"{rel}: credential_roots: {r!r} is the platform's own config, not an organization's")
        creds = doc.get("credentials")
        if creds is not None and not isinstance(creds, dict):
            problems.append(f"{rel}: credentials: is not a mapping of id to {{paths, acts_as, keeper}}")
            creds = {}
        for cid, c in (creds or {}).items():
            where = f"{rel}: credential {cid}"
            if not ID_RE.match(str(cid)):
                problems.append(f"{where}: the id must match {ID_RE.pattern}")
            if not isinstance(c, dict):
                problems.append(f"{where}: not a mapping of paths, acts_as and keeper")
                continue
            paths, ref = c.get("paths"), c.get("ref")
            if ref is not None:  # docs/agent-secrets.md §5.1: 1Password's secret reference, never a value
                import of_server_secrets
                m = of_server_secrets.REF_RE.match(ref) if isinstance(ref, str) else None
                if not m:
                    problems.append(f"{where}: ref must be a secret reference op://<vault>/<item>/<field> in letters,"
                                    " digits, - _ . and spaces")
                elif not m.group(1).lower().startswith(f"{str(doc.get('organization') or org).lower()}-"):
                    problems.append(f"{where}: ref's vault {m.group(1)!r} is not one of {org}'s (<org>-<system>)")
                elif ref.lower() in refs:
                    problems.append(f"{where}: ref names the same item as credential {refs[ref.lower()]}")
                refs.setdefault(ref.lower(), cid)
            if c.get("rotate_days") is not None and not (isinstance(c["rotate_days"], int) and c["rotate_days"] > 0):
                problems.append(f"{where}: rotate_days must be a whole number of days")
            if paths is None and ref is not None:
                paths = []
            elif not isinstance(paths, list) or not paths:
                problems.append(f"{where}: needs ref (a secret reference) or paths (a non-empty list of file paths)")
                paths = []
            for p in paths:
                if not isinstance(p, str) or not p.startswith(("~/", "/", "$HOME/")) or "\n" in p or " " in p.strip():
                    problems.append(f"{where}: {str(p)[:12]!r}… is not a file path (~/… or /…); the catalogue never "
                                    "holds a value")
                    continue
                a = _abs(p)
                if a in claimed and claimed[a] != (org, cid):
                    problems.append(f"{where}: path {p} is also claimed by {claimed[a][0]}:{claimed[a][1]}")
                claimed.setdefault(a, (org, cid))
            if c.get("keeper") is None:
                problems.append(f"{where}: no keeper (the position that owns its scope and rotation)")
            elif str(c.get("keeper")) not in names:
                problems.append(f"{where}: keeper {c.get('keeper')!r} is not a position of {org}")
            if c.get("acts_as") is not None and not isinstance(c.get("acts_as"), str):
                problems.append(f"{where}: acts_as must be text (what the credential acts as)")
        problems += authorities_problems(rel, doc.get("authorities"))
        for p in doc.get("positions") or []:
            if not isinstance(p, dict) or p.get("credentials") is None:
                continue
            ids = p.get("credentials")
            if not isinstance(ids, list):
                problems.append(f"{rel}: {p.get('name')}: credentials must be a list of credential ids")
                continue
            for i in ids:
                if str(i) not in (creds or {}):
                    problems.append(f"{rel}: {p.get('name')}: credential {i!r} is not in this organization's "
                                    "credentials: (a position lists only its own organization's)")
    # two credentials whose patterns both match one file on this machine (globs can overlap where ids differ)
    cat = load(orgs_file)
    pats = [(o, cid, p) for o, e in cat.items() for cid, c in e["credentials"].items() for p in c["paths"]
            if any(ch in p for ch in "*?[")]
    if pats:
        import glob as _glob
        seen = {}
        for o, cid, p in pats:
            for f in _glob.glob(p, recursive=True)[:5000]:
                for o2, cid2 in [seen.get(f)] if f in seen else []:
                    if (o2, cid2) != (o, cid):
                        problems.append(f"{o}:registry/positions.yaml: credential {cid}: {f} is also matched by "
                                        f"{o2}:{cid2}")
                seen.setdefault(f, (o, cid))
                for a, (o2, cid2) in claimed.items():
                    if a == f and (o2, cid2) != (o, cid):
                        problems.append(f"{o}:registry/positions.yaml: credential {cid}: {f} is also claimed by "
                                        f"{o2}:{cid2}")
    return problems


# ------------------------------------------------------------------------------------------- report (replay)

def _sessions(conn, since):
    who = {}
    for sid, pos, o, _n in conn.execute("SELECT session_id, position, org, count(*) FROM event WHERE position IS NOT "
                                        "NULL AND ts >= ? GROUP BY 1, 2, 3 ORDER BY 4", (since,)):
        who[sid] = (pos, o)
    return who


def report(days=21, db=None, orgs_file=None, out_dir=None, since=None, roots=None):
    """Replay recent sessions: every file under an organization's root (or named by a credential) that a tool call
    read or used, by position, and what the catalogue as it stands would decide. Writes one TSV per organization
    (mode 600; paths and counts, never a command or a value) and returns {org: summary}."""
    import of_guard
    import of_store
    since = since or (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S")
    cat = load(orgs_file)
    for org, root in (roots or {}).items():  # a root its registry does not name yet (`--root org=~/.config/x`)
        if org in cat:
            cat[org]["roots"] = [_abs(root)]
    keep = interesting(cat)
    conn = of_store.connect(db)
    who = _sessions(conn, since)
    tools = ("Bash", "Read", "Grep", "Edit", "MultiEdit", "NotebookEdit")
    q = ("SELECT session_id, blob_sha256, timestamp FROM transcript_record WHERE timestamp >= ? AND ("
         + " OR ".join("tool_names LIKE ?" for _ in tools) + ")")
    files = collections.defaultdict(lambda: {"positions": collections.Counter(), "days": set(), "denied": 0,
                                             "denied_by": collections.Counter(), "calls": 0, "ids": [], "org": None})
    calls = collections.Counter()
    denied_calls = collections.Counter()
    seen = set()
    for sid, sha, ts in conn.execute(q, (since, *[f"%{t}%" for t in tools])):
        pos, porg = who.get(sid, (None, None))
        try:
            rec = json.loads(of_store.get_blob(conn, sha) or b"{}")
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        content = (rec.get("message") or {}).get("content")
        for b in content if isinstance(content, list) else []:
            if not (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in tools):
                continue
            key = (sid, b.get("id"))
            if key in seen:
                continue
            seen.add(key)
            try:
                refs = of_guard.credential_refs(b["name"], b.get("input") or {}, rec.get("cwd"), keep)
            except Exception:  # noqa: BLE001 - one odd command never stops the replay
                continue
            hit_orgs = set()
            for path, _how in dict.fromkeys(refs):
                d = decide(path, pos, porg, cat)
                if d is None:
                    continue
                ok, owner, ids = d
                f = files[path]
                f["org"], f["ids"] = owner, ids
                f["positions"][pos or "-"] += 1
                f["days"].add((ts or "")[:10])
                f["calls"] += 1
                f["denied"] += 0 if ok else 1
                if not ok:
                    f["denied_by"][pos or "-"] += 1
                hit_orgs.add((owner, ok))
            for owner, ok in hit_orgs:
                calls[owner] += 1
                denied_calls[owner] += 0 if ok else 1
    conn.close()
    out_dir = out_dir or os.path.join(os.path.expanduser("~/.local/state/of"), "credentials")
    os.makedirs(out_dir, exist_ok=True)
    summary = {}
    for org, ent in cat.items():
        rows = [(p, f) for p, f in files.items() if f["org"] == org]
        if not rows and not ent.get("roots"):
            continue
        root = (ent.get("roots") or [""])[0]
        path = os.path.join(out_dir, f"{org}-reads.tsv")
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            fh.write("# file\tcredential-like name (G1)\tcredential ids\texists now\tcalls\twould deny\tdays\t"
                     "first day\tlast day\tpositions (calls)\twould deny (positions)\n")
            for p, f in sorted(rows, key=lambda x: (-x[1]["calls"], x[0])):
                rel = os.path.relpath(p, root) if _under([root], p) else p.replace(os.path.expanduser("~"), "~", 1)
                credlike = "yes" if of_guard._secret_path(p) else ""
                fh.write("\t".join([rel, credlike, ",".join(f["ids"]), "yes" if os.path.exists(p) else "no",
                                    str(f["calls"]), str(f["denied"]), str(len(f["days"])), min(f["days"]),
                                    max(f["days"]), " ".join(f"{k}:{v}" for k, v in f["positions"].most_common()),
                                    " ".join(f"{k}:{v}" for k, v in f["denied_by"].most_common())]) + "\n")
        pos_counter = collections.Counter()
        for _, f in rows:
            pos_counter.update(f["positions"])
        summary[org] = {"root": ", ".join(ent.get("roots") or []), "file": path, "files": len(rows),
                        "credential_like": sum(1 for p, _ in rows if of_guard._secret_path(p)),
                        "calls": calls[org], "would_deny": denied_calls[org],
                        "positions": len(pos_counter), "top_positions": pos_counter.most_common(8)}
    return summary


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="the credentials catalogue: check it, or replay recent sessions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    r = sub.add_parser("report", help="files under each organization's root that its positions read, by position")
    r.add_argument("--days", type=int, default=21)
    r.add_argument("--since")
    r.add_argument("--out-dir")
    r.add_argument("--db")
    r.add_argument("--root", action="append", default=[], metavar="ORG=PATH",
                   help="an organization's credential root its registry does not name yet (credential_roots)")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        probs = check()
        for p in probs:
            print(p)
        print(f"credentials: {len(probs)} problem(s)" if probs else "credentials: ok")
        return 1 if probs else 0
    s = report(a.days, a.db, out_dir=a.out_dir, since=a.since, roots=dict(r.split("=", 1) for r in a.root))
    for org, x in s.items():
        print(f"{org}: root {x['root'] or '(none)'}: {x['files']} files read or used by {x['positions']} positions "
              f"in {x['calls']} calls ({x['credential_like']} with a credential name); {x['would_deny']} calls would "
              f"be denied with the catalogue as it stands. List: {x['file']}")
        for p, n in x["top_positions"]:
            print(f"    {n:6} {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
