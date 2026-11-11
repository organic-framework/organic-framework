"""of import github: every merged PR as a work record (RFC 0001 v3 §5.1, observe gap 2).

For each repository named under `github:` in an organization's systems map, read merged PRs
with `gh` and write one `of.work_record` per PR: repository, number, author, times, head branch,
the systems it touched (changed paths matched against the systems' `paths:`, else the systems
that list the repository), and the author position where a transcript pr-link names the PR.
Deterministic ids: re-running updates, never duplicates.
"""

import datetime as dt
import os
import re
import json
import subprocess
import uuid

import of_events
import of_hook
import of_store

NS = uuid.UUID("2a8d7c11-4b6e-4f0a-8c3e-1d2e3f405162")
FIELDS = "number,author,createdAt,mergedAt,headRefName,title,files,url"
BODY_LIMIT = 2000  # bytes of a PR body or a commit message kept on the work record, masked (of_hook.clip)


def _commit_body(c):
    """The commit message below its subject line, masked and clipped; None when there is none."""
    msg = (c.get("message") or "").strip("\n")
    title = (c.get("title") or "").strip()
    if title and msg.startswith(title):
        msg = msg[len(title):]
    msg = msg.strip()
    return of_hook.clip(msg, BODY_LIMIT)[0] if msg else None


_CLI = []


def _of_cli():
    """bin/of as a module (glob_regex), loaded once."""
    if not _CLI:
        import importlib.machinery
        import importlib.util
        import os
        here = os.path.dirname(os.path.realpath(__file__))
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(here, "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        _CLI.append(mod)
    return _CLI[0]


def systems_by_repo(systems):
    """{github repo: [(system id, [path regexes], governed_by)]} from a systems map."""
    of = _of_cli()
    def listed(s):
        gh = s.get("github") or []
        return gh if isinstance(gh, list) else [gh]
    repos = {r for s in (systems or {}).values() if isinstance(s, dict) for r in listed(s)}
    out = {}
    for sid, s in (systems or {}).items():
        if not isinstance(s, dict):
            continue
        heads = {str(p).split("/", 1)[0] for p in s.get("paths") or []}
        # a system is a candidate for a repository it lists under `github:`, or whose directory
        # its paths start with (storefront-hosts claims storefront/scripts/ops/shop_*/** without
        # listing acme-inc/storefront)
        for repo in sorted(set(listed(s)) | {r for r in repos if r.split("/")[-1] in heads}):
            out.setdefault(repo, []).append((sid, [of.glob_regex(str(p)) for p in s.get("paths") or []],
                                             s.get("governed_by") or "self"))
    return out


def assign(repo, files, by_repo, local_dir):
    """Systems a PR touched: by changed paths (prefixed with the local repo directory name, as
    systems' paths are written), else the systems that claim the whole repository (no paths);
    otherwise none, and the record shows `systems: []` for the keeper to resolve."""
    cands = by_repo.get(repo, [])
    hits = {}
    for f in files:
        path = f"{local_dir}/{f}" if local_dir else f
        for sid, rxs, _ in cands:
            if any(rx.match(path) for rx in rxs):
                hits[sid] = hits.get(sid, 0) + 1
    if hits:
        return sorted(hits, key=lambda s: -hits[s])
    return [sid for sid, rxs, _ in cands if not rxs]  # only systems that claim the whole repository


def _ref(spec):
    """'storefront:docs/rfc/x.md (PR #1)' -> ('storefront', 'docs/rfc/x.md'); a plain glob -> (None, glob)."""
    s = str(spec or "").strip().split()
    head, sep, tail = (s[0] if s else "").partition(":")
    return (head, tail) if sep else (None, head)


def _same_repo(prefix, repo):
    """A registry's repository prefix ('storefront', 'acme/org', 'Owner/storefront') against a GitHub repo."""
    a, b = str(prefix or "").lower(), str(repo or "").lower()
    return bool(a) and (a == b or a.rpartition("/")[2] == b.rpartition("/")[2])


QUEUE_BRANCHES = ("mergify/merge-queue/", "gh-readonly-queue/")
MATCH_VERSION = "2"  # 2: '<repo>:<glob>' branches, merge-queue batches never match; a new version re-matches all


def match_works(works, repo, number, head_ref, files, repo_systems=()):
    """The ids of the works a PR belongs to, by the work's `match` (docs/registry.md, Works): its PR numbers in the
    doc's repository, its branch globs ('<repo>:<glob>' in that repository; a bare glob in the work's repositories:
    the doc's, or one its systems live in), its
    '<repo>:<glob>' or plain paths (plain ones as systems' paths are written, '<repo dir>/<path>'), and a change to
    its doc file. A work set done or parked still matches: the evidence is history."""
    import fnmatch
    if str(head_ref or "").startswith(QUEUE_BRANCHES):
        return []  # a merge queue's batch PR copies PRs already counted (acme_org, 2026-11-06)
    of = _of_cli()
    local = str(repo or "").rpartition("/")[2]
    out = []
    for wid, w in sorted((works or {}).items()):
        if not isinstance(w, dict):
            continue
        mt = w.get("match") if isinstance(w.get("match"), dict) else {}
        doc_repo, _ = _ref(mt.get("doc") or w.get("doc"))
        in_repo = (_same_repo(doc_repo, repo) if doc_repo else False) or bool(set(w.get("systems") or []) & set(repo_systems))
        hit = number is not None and (not doc_repo or _same_repo(doc_repo, repo)) and \
            int(number) in {int(x) for x in mt.get("prs") or [] if str(x).isdigit()}
        for g in mt.get("branches") or []:  # '<repo>:<glob>' in that repository; a bare glob in the work's own
            if hit or not head_ref:          # (git refs never hold ':', so the prefix is unambiguous)
                break
            r_, glob = _ref(g) if ":" in str(g) else (None, str(g))
            hit = bool(glob) and fnmatch.fnmatchcase(head_ref, glob) and (
                _same_repo(r_, repo) if r_ else (in_repo or not (doc_repo or w.get("systems"))))
        for g in mt.get("paths") or []:
            if hit:
                break
            r_, glob = _ref(g)
            if not glob:
                continue
            rx = of.glob_regex(glob)
            hit = any(rx.match(f) for f in files) if r_ and _same_repo(r_, repo) else \
                (not r_ and any(rx.match(f"{local}/{f}") for f in files))
        for spec in (mt.get("doc"), w.get("doc")):
            r_, path = _ref(spec)
            if not hit and r_ and "/" in path and _same_repo(r_, repo) and path in files:
                hit = True
        if hit:
            out.append(wid)
    return out


def author_of(pr, link, known=None):
    """(position, attribution) of a PR: a transcript pr-link first, else the PR body's `Position:` line, which every
    agent PR carries (AGENTS.md). of_field-analyst, 2026-11-10: only 90 of 820 merged Acme PRs had a position,
    from pr-links alone, so fragmented-ownership, over-coupled and chatty's pair part stayed blocked. `known`: the
    organization's position names; a line naming no position is ignored. The last line wins (a reviewed PR may
    quote another's)."""
    if link:
        return link[0], "pr-link"
    found = pr["position_lines"] if "position_lines" in pr else POSITION_LINE.findall(pr.get("body") or "")
    names = [x for x in found if known is None or x in known]
    return (names[-1], "trailer") if names else (None, None)


def pr_links(conn):
    """{(repo lower, number): (position, org, session)} from imported pr-link work records."""
    out = {}
    for pos, org, sid, payload in conn.execute(
            "SELECT position, org, session_id, payload FROM event WHERE name = 'of.work_record' AND payload LIKE '%pr-link%'"):
        p = json.loads(payload or "{}")
        if p.get("repo") and p.get("number"):
            out[(str(p["repo"]).lower(), int(p["number"]))] = (pos, org, sid)
    return out


def windows(since, today=None, days=3):
    """[(start, end)] date windows from since to today, inclusive, so one gh call stays small
    (a large page of PRs with their files makes GitHub return a cut response)."""
    import datetime as dt
    d = dt.date.fromisoformat(since)
    end = today or dt.date.today()
    out = []
    while d <= end:
        e = min(d + dt.timedelta(days=days - 1), end)
        out.append((d.isoformat(), e.isoformat()))
        d = e + dt.timedelta(days=1)
    return out


POSITIONS_QUERY = """query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes { ... on PullRequest { number body commits(last: 50) { nodes { commit { message } } }
                                 reviews(states: [APPROVED, CHANGES_REQUESTED, COMMENTED, DISMISSED]) { totalCount } } }
  }
}"""


def bodies(repo, state, search=None, gh="gh", pages=40, details=None):
    """{PR number: the Position lines of its body and its commits}, by a separate GraphQL search (50 PRs a page):
    bodies or commits together with files cut GitHub's answer on a busy window (2026-11-10: storefront 10-28..10-30,
    "unexpected end of JSON input"), and agents write the trailer in their commits more often than in the PR body
    (#3093: none in the body, two in its commits). Best effort: what was read before a failure, never an error.
    details: a dict to fill with {PR number: {body, reviews}} (the number of submitted reviews), for the work record
    (of_field-analyst, 2026-11-11: ap.unsourced-requirement reads what a PR claimed and whether anyone reviewed it)."""
    q = f"repo:{repo} is:pr " + {"merged": "is:merged", "open": "is:open", "closed": "is:closed"}.get(state, "")
    q += f" {search}" if search else ""
    out, after = {}, None
    for _ in range(pages):
        argv = [gh, "api", "graphql", "-f", f"query={POSITIONS_QUERY}", "-f", f"q={q}"]
        if after:
            argv += ["-f", f"after={after}"]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                break
            data = json.loads(r.stdout or "{}")["data"]["search"]
        except Exception:  # noqa: BLE001
            break
        for n in data.get("nodes") or []:
            if not n or "number" not in n:
                continue
            texts = [n.get("body") or ""] + [(c.get("commit") or {}).get("message") or ""
                                             for c in (n.get("commits") or {}).get("nodes") or []]
            out[int(n["number"])] = [x for t in texts for x in POSITION_LINE.findall(t)]
            if details is not None:
                details[int(n["number"])] = {"body": n.get("body") or "",
                                             "reviews": (n.get("reviews") or {}).get("totalCount")}
        if not (data.get("pageInfo") or {}).get("hasNextPage"):
            break
        after = data["pageInfo"]["endCursor"]
    return out


def fetch(repo, since, gh="gh", attempts=4):
    """gh pr list per date window, retried with backoff on transient GitHub errors (5xx, timeouts,
    cut responses)."""
    import time
    out, seen = [], set()
    for a, b in windows(since):
        err = ""
        for i in range(attempts):
            r = subprocess.run([gh, "pr", "list", "--repo", repo, "--state", "merged", "--search",
                                f"merged:{a}..{b}", "--limit", "1000", "--json", FIELDS],
                               capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                break
            err = r.stderr.strip()[:200]
            if not any(t in err for t in ("HTTP 5", "timeout", "EOF", "connection", "JSON")):
                raise RuntimeError(f"gh pr list {repo} {a}..{b}: {err}")
            time.sleep(5 * 2 ** i)
        else:
            raise RuntimeError(f"gh pr list {repo} {a}..{b}: {err}")
        details = {}
        lines = bodies(repo, "merged", f"merged:{a}..{b}", gh, details=details)
        for pr in json.loads(r.stdout or "[]"):
            if pr["number"] not in seen:
                seen.add(pr["number"])
                out.append(dict(pr, position_lines=lines.get(int(pr["number"]), []),
                                **details.get(int(pr["number"]), {})))
    return out


def last_merged(conn, org):
    """The last imported merge of the organization (ISO), or None."""
    row = conn.execute("SELECT max(ts) FROM event WHERE name = 'of.work_record' AND org = ? "
                       "AND json_extract(payload, '$.source') = 'gh'", (org,)).fetchone()
    return row[0] if row else None


def since_for(conn, org, default="2026-10-01"):
    """Incremental: the day before the last imported merge (windows are whole days; the deterministic
    event id makes a re-read PR replace its own record, never a second one)."""
    last = last_merged(conn, org)
    if not last:
        return default
    d = dt.date.fromisoformat(last[:10]) - dt.timedelta(days=1)
    return d.isoformat()


def has_repos(systems):
    return bool(systems_by_repo(systems or {}))


def works_digest(works):
    """A stable hash of an organization's works map: what matching reads (match, doc, systems)."""
    import hashlib
    keep = {k: {f: (w or {}).get(f) for f in ("match", "doc", "systems")} for k, w in sorted((works or {}).items())
            if isinstance(w, dict)}
    return hashlib.sha256(json.dumps([MATCH_VERSION, keep], sort_keys=True, default=str).encode()).hexdigest()[:16]


def _digest_path():
    import os
    return os.path.join(of_events.STATE_DIR, "works-match.json")


def rematch(conn, org, systems, works, log=print, force=False, batch=200):
    """When the works map changed since the last match (acme_org sharpens a glob), recompute `works` on the
    organization's stored of.work_record and of.pr_state from their own fields (repo, number, head_ref, paths), with
    no GitHub call, so attribution is never stale (of_studio, 2026-11-06). Short write transactions. Returns the
    number of records whose works changed. The local store only: a copy shipped earlier keeps its old list."""
    try:
        with open(_digest_path()) as fh:
            seen = json.load(fh)
    except (OSError, ValueError):
        seen = {}
    dig = works_digest(works)
    if not force and seen.get(org) == dig:
        return 0
    by_repo = systems_by_repo(systems)
    changes = []
    for eid, pl in conn.execute("SELECT event_id, payload FROM event WHERE org = ? AND name IN "
                                "('of.work_record', 'of.pr_state')", (org,)):
        p = json.loads(pl or "{}")
        if p.get("source") != "gh" or not p.get("repo"):
            continue
        new = match_works(works, p["repo"], p.get("number"), p.get("head_ref"), p.get("paths") or [],
                          [c[0] for c in by_repo.get(p["repo"], [])])
        if new != p.get("works"):
            changes.append((json.dumps(new), eid))
    for i in range(0, len(changes), batch):
        with of_store.write_tx(conn):
            for w, eid in changes[i:i + batch]:
                conn.execute("UPDATE event SET payload = json_set(payload, '$.works', json(?)), "
                             "record = json_set(record, '$.\"of.payload\".works', json(?)) WHERE event_id = ?",
                             (w, w, eid))
    seen[org] = dig
    try:
        import os
        tmp = _digest_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(seen, fh)
        os.replace(tmp, _digest_path())
    except OSError:
        pass
    log(f"  works map changed: {len(changes)} records re-matched")
    return len(changes)


def import_org(conn, org, systems, since, fetcher=fetch, log=print, position_system=None, works=None,
               open_fetcher=None, closed_fetcher=None, detail_fetcher=None):
    """position_system: {position name: system id}, for PRs that touch only shared paths. works: the organization's
    `works:` map; each record names the works its PR matches. open_fetcher, closed_fetcher: also write `of.pr_state`
    for open PRs and for PRs closed without a merge (see pr_states)."""
    position_system = position_system or {}
    if works is not None:
        rematch(conn, org, systems, works, log)
    by_repo = systems_by_repo(systems)
    links = pr_links(conn)
    governed = {sid: g for cands in by_repo.values() for sid, _, g in cands}
    n = 0
    for repo in sorted(by_repo):
        prs = fetcher(repo, since)
        local_dir = repo.split("/")[-1]
        for pr in prs:
            files = [f.get("path") for f in pr.get("files") or [] if f.get("path")]
            sys_ = assign(repo, files, by_repo, local_dir)
            link = links.get((repo.lower(), int(pr["number"])))
            who, how = author_of(pr, link, set(position_system) or None)
            basis = "paths" if sys_ else "none"
            if not sys_ and who and position_system.get(who) in (systems or {}):
                sys_, basis = [position_system[who]], "position"  # shared paths only: the author's system
            payload = {"source": "gh", "repo": repo, "number": pr["number"], "url": pr.get("url"),
                       "author": (pr.get("author") or {}).get("login"), "created_at": pr.get("createdAt"),
                       "merged_at": pr.get("mergedAt"), "head_ref": pr.get("headRefName"),
                       "title": of_hook.clip(pr.get("title") or "", 200)[0], "files": len(files),
                       "body": of_hook.clip(pr["body"], BODY_LIMIT)[0] if pr.get("body") else None,
                       "reviews": pr.get("reviews"),
                       "paths": files[:300], "systems": sys_, "system_basis": basis,
                       "external": [s for s in sys_ if str(governed.get(s, "self")).startswith("external:")],
                       "position": who, "attribution": how,
                       "works": match_works(works, repo, pr["number"], pr.get("headRefName"), files,
                                            [c[0] for c in by_repo.get(repo, [])])}
            rec = of_events.record("work_record", payload, position=who, org=org,
                                   session=link[2] if link else None, source="import",
                                   extra={"of.resolution": f"registry@{of_events.now_iso()[:10]}"})
            rec["of.event.id"] = str(uuid.uuid5(NS, f"{repo.lower()}#{pr['number']}:gh"))
            rec["timestamp"] = pr.get("mergedAt") or rec["timestamp"]
            conn.execute("DELETE FROM event_fts WHERE event_id = ?", (rec["of.event.id"],))
            conn.execute("DELETE FROM event WHERE event_id = ?", (rec["of.event.id"],))
            of_store.add_event(conn, rec)
            n += 1
        log(f"  {repo}: {len(prs)} merged PRs")
        if open_fetcher:
            try:  # PR states are extra: a failure here never stops the merged records of the next repository
                k = pr_states(conn, org, repo, open_fetcher(repo), (closed_fetcher or (lambda r, s_: []))(repo, since),
                              by_repo, works, links, detail=detail_fetcher, known=set(position_system) or None)
                log(f"  {repo}: {k} PR state changes")
            except Exception as e:  # noqa: BLE001
                log(f"  {repo}: PR states skipped: {type(e).__name__}: {str(e)[:200]}")
    log(f"{n} work records for {org}")
    return n


OPEN_FIELDS = "number,author,createdAt,updatedAt,headRefName,title,url,isDraft,latestReviews"
DETAIL_FIELDS = "files,commits"  # per changed PR only: in a list they exceed GitHub's GraphQL node limit
CLOSED_FIELDS = "number,author,createdAt,updatedAt,closedAt,mergedAt,headRefName,title,url,isDraft"


def _gh_json(argv, attempts=4):
    import time
    err = ""
    for i in range(attempts):
        r = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        if r.returncode == 0:
            return json.loads(r.stdout or "[]")
        err = r.stderr.strip()[:200]
        if not any(t in err for t in ("HTTP 5", "timeout", "EOF", "connection", "JSON")):
            break
        time.sleep(5 * 2 ** i)
    raise RuntimeError(f"{' '.join(argv[1:5])}: {err}")


def fetch_open(repo, gh="gh"):
    lines = bodies(repo, "open", None, gh)
    return [dict(pr, position_lines=lines.get(int(pr["number"]), [])) for pr in
            _gh_json([gh, "pr", "list", "--repo", repo, "--state", "open", "--limit", "300", "--json", OPEN_FIELDS])]


def fetch_detail(repo, number, gh="gh"):
    """files and commits of one PR (called only for a PR whose state changed)."""
    return _gh_json([gh, "pr", "view", str(number), "--repo", repo, "--json", DETAIL_FIELDS]) or {}


def fetch_closed(repo, since, gh="gh"):
    """PRs closed since `since` (merged ones too; pr_states keeps the unmerged)."""
    out, seen = [], set()
    for a, b in windows(since):
        lines = bodies(repo, "closed", f"closed:{a}..{b}", gh)
        for pr in _gh_json([gh, "pr", "list", "--repo", repo, "--state", "closed", "--search", f"closed:{a}..{b}",
                            "--limit", "1000", "--json", CLOSED_FIELDS]):
            if pr["number"] not in seen:
                seen.add(pr["number"])
                out.append(dict(pr, position_lines=lines.get(int(pr["number"]), [])))
    return out


def pr_states(conn, org, repo, open_prs, closed_prs, by_repo, works, links=None, detail=None, known=None):
    """One `of.pr_state` per change of an open PR (or its close without a merge), for Works' "moving" (docs/works.md
    §3: open-PR activity counts as progress): repo, number, head_ref, author, state open|closed, draft, updated_at,
    last_review_at (the newest submitted review), last_push_at (the newest commit date on the head: GitHub's list
    API has no push time), paths, systems, works. The id is deterministic over (PR, updated_at): an unchanged PR
    writes nothing on the next run. Merged PRs are `of.work_record`s."""
    links = links or {}
    local = repo.split("/")[-1]
    n = 0
    for pr, state in [(p, "open") for p in open_prs] + [(p, "closed") for p in closed_prs if not p.get("mergedAt")]:
        eid = str(uuid.uuid5(NS, f"{repo.lower()}#{pr['number']}:pr_state:{pr.get('updatedAt')}:{state}"))
        if conn.execute("SELECT 1 FROM event WHERE event_id = ?", (eid,)).fetchone():
            continue
        if detail and state == "open" and "files" not in pr:
            try:
                pr = dict(pr, **detail(repo, pr["number"]))
            except Exception:  # noqa: BLE001 - a state without paths beats no state
                pass
        files = [f.get("path") for f in pr.get("files") or [] if f.get("path")]
        reviews = [r.get("submittedAt") for r in pr.get("latestReviews") or [] if r.get("submittedAt")]
        commits = [c.get("committedDate") for c in pr.get("commits") or [] if c.get("committedDate")]
        link = links.get((repo.lower(), int(pr["number"])))
        who, _ = author_of(pr, link, known)
        payload = {"source": "gh", "repo": repo, "number": pr["number"], "url": pr.get("url"),
                   "title": of_hook.clip(pr.get("title") or "", 200)[0], "author": (pr.get("author") or {}).get("login"),
                   "head_ref": pr.get("headRefName"), "state": state, "draft": bool(pr.get("isDraft")),
                   "created_at": pr.get("createdAt"), "updated_at": pr.get("updatedAt"), "closed_at": pr.get("closedAt"),
                   "last_review_at": max(reviews) if reviews else None, "last_push_at": max(commits) if commits else None,
                   "paths": files[:300], "systems": assign(repo, files, by_repo, local) if files else [],
                   "works": match_works(works, repo, pr["number"], pr.get("headRefName"), files,
                                        [c[0] for c in by_repo.get(repo, [])]),
                   "position": who}
        rec = of_events.record("pr_state", payload, position=who, org=org,
                               session=link[2] if link else None, source="import")
        rec["of.event.id"] = eid
        rec["timestamp"] = pr.get("updatedAt") or rec["timestamp"]
        of_store.add_event(conn, rec)
        n += 1
    return n


# ------------------------------------------------------------------ local commits (of import git)

POSITION_LINE = re.compile(r"(?m)^Position:\s*([a-z][a-z0-9_-]{0,31})\s*$")
GIT_SEP, REC_SEP = "\x1f", "\x1e"


def systems_by_dir(systems):
    """{local repository directory: [(system id, [path regexes])]}: a system's `repos:` (whole repository when it has
    no paths) and the directories its `paths:` start with, as the systems' paths are written ('<dir>/<path>')."""
    of = _of_cli()
    out = {}
    for sid, sy in (systems or {}).items():
        if not isinstance(sy, dict):
            continue
        rxs = [of.glob_regex(str(x)) for x in sy.get("paths") or []]
        dirs = {str(x).split("/", 1)[0] for x in sy.get("paths") or []} | {str(r).rpartition("/")[2] for r in sy.get("repos") or []}
        for d in dirs:
            out.setdefault(d, []).append((sid, rxs))
    return out


DEFAULT_CANDIDATES = ("main", "master", "trunk", "develop")


def default_branch(root, override=None, git="git"):
    """(ref, how) for the checkout's default branch, or (None, why not). The founder, 2026-11-11: of import git read
    only `main`, and a repository's default may be master, develop, trunk or another. In order: the branch orgs.yaml
    names for this repository (`branches:` on the organization's entry); the remote's default (origin/HEAD), as the
    local branch of that name when it exists; else the one of main, master, trunk, develop that exists."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")

    def run(*a):
        return subprocess.run([git, "-C", root, *a], capture_output=True, text=True, timeout=30, env=env)

    def exists(ref):
        return run("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0
    if override:
        return (override, "orgs.yaml") if exists(override) else \
            (None, f"the branch {override!r} that orgs.yaml names does not exist here")
    r = run("symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip():
        remote = r.stdout.strip()                      # origin/<name>
        name = remote.split("/", 1)[1] if "/" in remote else remote
        return (name, "origin/HEAD") if exists(f"refs/heads/{name}") else (remote, "origin/HEAD")
    found = [b for b in DEFAULT_CANDIDATES if exists(f"refs/heads/{b}")]
    if len(found) == 1:
        return found[0], "the only one of " + ", ".join(DEFAULT_CANDIDATES)
    if found:
        return None, (f"no origin/HEAD and several candidates ({', '.join(found)}); name the default in orgs.yaml "
                      "(branches: {<repository>: <branch>} on the organization's entry)")
    return None, ("no origin/HEAD and none of " + ", ".join(DEFAULT_CANDIDATES) + "; name the default in orgs.yaml "
                  "(branches: {<repository>: <branch>} on the organization's entry)")


def local_commits(root, since, git="git", branch="main"):
    """[{sha, author, at, title, message, paths}] on `branch` of the checkout at `root` (its default branch, from
    default_branch), since the ISO date (oldest first). Read-only: no locks, no prompts, no user config."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    fmt = GIT_SEP.join(["%H", "%an", "%ct", "%s", "%B"]) + REC_SEP
    r = subprocess.run([git, "-C", root, "-c", "core.quotepath=false", "log", branch, "--reverse", f"--since={since}",
                        f"--format={fmt}", "--name-only"], capture_output=True, text=True, timeout=120, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"git log {root}: {r.stderr.strip()[:200]}")
    out = []
    for chunk in r.stdout.split(REC_SEP):
        chunk = chunk.lstrip("\n")
        if not chunk.strip():
            continue
        if not out and GIT_SEP not in chunk:
            continue
        # the file list of a commit follows its record separator, before the next commit's fields
        parts = chunk.split(GIT_SEP)
        if len(parts) < 5:
            if out:
                out[-1]["paths"] += [x for x in chunk.split("\n") if x.strip()]
            continue
        files_of_prev = parts[0].split("\n")
        if out and len(files_of_prev) > 1:
            out[-1]["paths"] += [x for x in files_of_prev[:-1] if x.strip()]
            parts[0] = files_of_prev[-1]
        sha, author, title, message = parts[0].strip(), parts[1], parts[3], GIT_SEP.join(parts[4:])
        at = dt.datetime.fromtimestamp(int(parts[2]), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # UTC, as events
        out.append({"sha": sha, "author": author, "at": at, "title": title, "message": message, "paths": []})
    return out


def last_local(conn, org):
    row = conn.execute("SELECT max(ts) FROM event WHERE name = 'of.work_record' AND org = ? "
                       "AND json_extract(payload, '$.source') = 'git'", (org,)).fetchone()
    return row[0] if row else None


def import_local(conn, org, systems, roots, since=None, works=None, log=print, lister=local_commits, branches=None,
                 resolve=default_branch):
    """Every commit on the default branch (default_branch; `branches` from orgs.yaml overrides it per repository) of
    the organization's checkouts (orgs.yaml paths) as of.work_record {source: git,
    unit: commit, repo, sha, author, merged_at, title, position, paths, systems, works}, for an organization that
    commits locally and never pushes (the OF team; of_studio, docs/studio.md §2.1). Deterministic id by repo and sha;
    an existing record is kept. Returns the number written."""
    if not since:
        last = last_local(conn, org)
        since = (dt.date.fromisoformat(last[:10]) - dt.timedelta(days=1)).isoformat() if last else "2026-10-01"
    by_dir = systems_by_dir(systems)
    if branches is not None and not isinstance(branches, dict):
        log(f"  branches in orgs.yaml for {org} is not a mapping of repository to branch; ignored")
        branches = None
    # keys by folder name or path, a path written as orgs.yaml writes paths (~/…) (of_studio's review of PR 12)
    branches = {os.path.expanduser(str(k)): v for k, v in (branches or {}).items()}
    n = 0
    for root in roots:
        root = os.path.expanduser(str(root))
        if not os.path.isdir(os.path.join(root, ".git")):
            continue  # a main checkout only (a worktree's .git is a file)
        repo = os.path.basename(root.rstrip("/"))
        ref, how = resolve(root, branches.get(repo) or branches.get(root.rstrip("/")) or branches.get(root))
        if not ref:
            log(f"  {repo}: not read: {how}")  # said plainly, never an empty read that looks like no work
            continue
        commits = lister(root, since, branch=ref)
        for c in commits:
            eid = str(uuid.uuid5(NS, f"{repo}@{c['sha']}:git"))
            if conn.execute("SELECT 1 FROM event WHERE event_id = ?", (eid,)).fetchone():
                continue
            m = POSITION_LINE.findall(c["message"] or "")
            pos = m[-1] if m else None
            files = c["paths"]
            hits = {}
            for sid, rxs in by_dir.get(repo, []):
                if not rxs:
                    hits[sid] = hits.get(sid, 0) + 1
                for f in files:
                    if any(rx.match(f"{repo}/{f}") for rx in rxs):
                        hits[sid] = hits.get(sid, 0) + 1
            payload = {"source": "git", "unit": "commit", "repo": repo, "sha": c["sha"], "author": c["author"],
                       "merged_at": c["at"], "title": of_hook.clip(c["title"] or "", 200)[0], "position": pos,
                       "message": _commit_body(c),
                       "paths": files[:300], "files": len(files),
                       "systems": sorted(hits, key=lambda x: -hits[x]),
                       "works": match_works(works, repo, None, None, files, list(hits))}
            rec = of_events.record("work_record", payload, position=pos, org=org, source="import",
                                   extra={"of.resolution": f"registry@{of_events.now_iso()[:10]}"})
            rec["of.event.id"] = eid
            rec["timestamp"] = c["at"]
            of_store.add_event(conn, rec)
            n += 1
        log(f"  {repo}: {len(commits)} commits on {ref} ({how}) since {since}")
    log(f"{n} work records (local commits) for {org}")
    return n
