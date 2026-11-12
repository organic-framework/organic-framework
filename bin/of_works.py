"""Works: following the work, not the agents (docs/works.md; of_studio's step 3).

v0 derives the works list itself, until the registry's `works:` map (docs/works.md §2, of_platform's schema) and
acme_org's confirmed map exist:
- RFC works: every `docs/rfc/*.md` in the main checkout of a repository under the organization's paths (a git
  worktree, whose `.git` is a file, is a copy and is skipped); title from the H1, stage from the `Status:` line;
- project works: every position of kind `project`; title from its summary, the end and review from its mandate;
- a position whose brief names an RFC file is a doer of that RFC's work, and a project whose brief names one RFC is
  that work (one row, not two).

State is derived (docs/works.md §3): progress is a merged PR of a doer or naming the RFC, a commit to the RFC file,
or a doer's turn; waits are the doers' (of_measures.attention). Nothing here is written anywhere.
"""

import collections
import functools
import os
import re
import subprocess
import time

import of_model
import of_measures

epoch = of_model.epoch
DAY = 86400.0
GIT_CACHE_S = 600        # repository reads (refs, logs) are reused for 10 minutes
ACTIVE_DAYS = 3          # progress in the last 3 days: active (docs/works.md §3)
WORKS_VERSION = "wk-v0"
RFC_LINK = re.compile(r"docs/rfc/([A-Za-z0-9._-]+?)\.md")
READING = re.compile(r"read first|reading|references|see also|background", re.I)


# ------------------------------------------------------------------ sources

def _is_main_checkout(path):
    return os.path.isdir(os.path.join(path, ".git"))


def rfc_dirs(org):
    """(repo name, repo path) for each main checkout under the organization's paths that has docs/rfc."""
    out, seen = [], set()
    for root in org.get("paths") or []:
        for cand in [root] + sorted(os.path.join(root, n) for n in (os.listdir(root) if os.path.isdir(root) else [])):
            real = os.path.realpath(cand)
            if real in seen or not os.path.isdir(os.path.join(cand, "docs", "rfc")) or not _is_main_checkout(cand):
                continue
            seen.add(real)
            out.append((os.path.basename(cand.rstrip("/")), cand))
    return out


def _head(path):
    title, status = None, None
    try:
        with open(path, encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if title is None and line.startswith("# "):
                    title = line[2:].strip()
                m = re.match(r"\**status\**:?\**\s*(.*)", line.strip(), re.I)
                if status is None and m and m.group(1):
                    status = m.group(1).strip()
                if (title and status) or i > 40:
                    break
    except OSError:
        pass
    return title, status


@functools.lru_cache(maxsize=16)
def _doc_commits(repo_path, minute):
    """{relative path: [commit epochs, newest first]} for docs/rfc over 60 days; cached per minute."""
    try:
        out = subprocess.run(["git", "-C", repo_path, "log", "origin/HEAD", "--since=60.days", "--name-only", "--format=@%cI",
                              "--", "docs/rfc"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    res, t = collections.defaultdict(list), None
    for line in out.splitlines():
        if line.startswith("@"):
            t = epoch(line[1:].replace("+00:00", "Z")) if line.endswith("+00:00") else _iso_any(line[1:])
        elif line.strip() and t:
            res[line.strip()].append(t)
    return dict(res)


def _iso_any(s):
    """'2026-11-06T17:07:51+04:00' -> epoch."""
    m = re.match(r"(.*)([+-])(\d\d):(\d\d)$", s)
    if not m:
        return epoch(s)
    base = epoch(m.group(1))
    off = (int(m.group(3)) * 3600 + int(m.group(4)) * 60) * (1 if m.group(2) == "+" else -1)
    return None if base is None else base - off


def rfc_works(org, now):
    out = []
    for repo, path in rfc_dirs(org):
        commits = _doc_commits(path, int(now // GIT_CACHE_S))
        d = os.path.join(path, "docs", "rfc")
        for name in sorted(os.listdir(d)):
            if not name.endswith(".md") or name.lower() == "readme.md" or re.search(r"\.[a-z]{2}\.md$", name):
                continue
            slug = name[:-3]
            title, status = _head(os.path.join(d, name))
            rel = f"docs/rfc/{name}"
            out.append({"id": f"{repo}:{slug}", "kind": "rfc", "title": title or slug, "status_line": status,
                        "doc": {"repo": repo, "path": rel}, "slug": slug, "doers": [], "systems": [],
                        "doc_commits": commits.get(rel, []), "source": "rfc file"})
    return out


def _brief_links(repo, positions):
    """{position: [rfc slugs its brief names]} from registry/briefs/<name>.md in the organization's repository."""
    out = {}
    for n in positions:
        try:
            with open(os.path.join(repo, "registry", "briefs", f"{n}.md"), encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        # a link under "Read first" (or references) is reading, not the position's work
        kept, skip = [], False
        for line in text.splitlines():
            if line.startswith("#"):
                skip = bool(READING.search(line))
            elif not skip:
                kept.append(line)
        slugs = sorted({m.group(1) for m in RFC_LINK.finditer("\n".join(kept))})
        if slugs:
            out[n] = slugs
    return out


def derive(model, org, now):
    """The works list v0 (see the module docstring)."""
    positions = model["positions"]
    rfcs = rfc_works(org, now)
    by_slug = collections.defaultdict(list)
    for w in rfcs:
        by_slug[w["slug"]].append(w)
    links = _brief_links(org.get("repo"), positions)
    works = []
    for n, p in sorted(positions.items()):
        if p.get("kind") != "project":
            continue
        ex = p.get("exit") if isinstance(p.get("exit"), dict) else {}
        w = {"id": n, "kind": "project", "title": p.get("summary") or n, "doers": [n], "systems": [p["system"]] if p.get("system") else [],
             "doc": {"repo": None, "path": f"registry/briefs/{n}.md"}, "review": str(ex.get("review")) if ex.get("review") else None,
             "done_when": ex.get("done_when"), "status": p.get("status"), "source": "project position"}
        mine = [r for s in links.get(n, []) for r in by_slug.get(s, [])]
        if len(mine) == 1:   # the project is that RFC's work: one row
            r = mine[0]
            r.update(doers=sorted(set(r["doers"]) | {n}), systems=sorted(set(r["systems"]) | set(w["systems"])),
                     review=w["review"], done_when=w["done_when"], status=w["status"], project=n,
                     source="rfc file + project position")
            continue
        works.append(w)
    for n, slugs in links.items():   # keepers and others whose brief names an RFC: its doers
        if positions.get(n, {}).get("kind") == "project":
            continue
        for s in slugs:
            for r in by_slug.get(s, []):
                r["doers"] = sorted(set(r["doers"]) | {n})
                r["systems"] = sorted(set(r["systems"]) | ({positions[n]["system"]} if positions[n].get("system") else set()))
    return works + rfcs


# ------------------------------------------------------------------ the registry's works (docs/works.md §2)

DOC_REF = re.compile(r"^\s*([A-Za-z0-9._-]+):([^\s(]+)")
DONE_STATES = {"done": "finished", "parked": "parked", "park": "parked"}


def registered(org):
    """The organization's confirmed works, or None: `works:` in registry/positions.yaml, else registry/works.yaml or a
    works.draft.yaml whose header says it is confirmed (acme_org's list until of_platform's schema lands)."""
    import yaml
    repo = org.get("repo") or ""
    for name, need_confirmed in (("positions.yaml", False), ("works.yaml", False), ("works.draft.yaml", True)):
        try:
            with open(os.path.join(repo, "registry", name), encoding="utf-8") as fh:
                doc = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        works = doc.get("works")
        if not isinstance(works, dict) or not works:
            continue
        status = str((doc.get("draft") or {}).get("status") or "")
        if need_confirmed and not status.lower().startswith("confirmed"):
            continue
        src = f"registry/{name}" + (" (confirmed)" if need_confirmed else "")
        return [_from_registry(wid, w, src) for wid, w in works.items() if isinstance(w, dict)]
    return None


def _from_registry(wid, w, src):
    m = DOC_REF.match(str(w.get("doc") or ""))
    repo, path = (m.group(1), m.group(2)) if m else (None, str(w.get("doc") or "") or None)
    match = w.get("match") or {}
    # `state: done|parked` is the keeper's end of the work and wins; `draft_state` (an org's own key) describes the
    # work's document and counts only while the work is not ended (acme_org, 2026-11-10)
    st = str(w.get("state") or "")
    if st not in DONE_STATES:
        st = str(w.get("draft_state") or st)
    return {"id": wid, "kind": w.get("kind") or "project", "title": w.get("title") or wid, "doc": {"repo": repo, "path": path},
            "slug": os.path.basename(path)[:-3] if path and path.endswith(".md") and "/rfc/" in path else None,
            "doers": [str(x) for x in w.get("doers") or []], "systems": list(w.get("systems") or []),
            "owner": w.get("owner"), "review": str(w["review"]) if w.get("review") else None, "done_when": w.get("done_when"),
            "why": w.get("why"), "declared_state": st or None, "declared_progress": w.get("last_progress"),
            "status": DONE_STATES.get(st), "stages": w.get("stages") or [], "status_line": w.get("why"),
            "match": {"prs": [int(x) for x in match.get("prs") or [] if str(x).isdigit()],
                      "branches": list(match.get("branches") or []), "paths": list(match.get("paths") or []),
                      "doc": match.get("doc")},
            "loop": w.get("loop") if isinstance(w.get("loop"), dict) else None,   # RFC 0005 §4
            "standing": w.get("standing") is True,   # docs/registry.md: no final end; its loop is its end condition
            "goal": str(w["goal"]) if w.get("goal") else None,                 # docs/registry.md, Goals: a leaf
            "condition": str(w["condition"]) if w.get("condition") else None,  # or the condition it keeps
            "source": src}


def checkouts(org):
    """{repository name: path} for every main checkout directly under the organization's paths."""
    out = {}
    for root in org.get("paths") or []:
        for cand in [root] + sorted(os.path.join(root, n) for n in (os.listdir(root) if os.path.isdir(root) else [])):
            if os.path.isdir(cand) and _is_main_checkout(cand):
                out.setdefault(os.path.basename(cand.rstrip("/")), cand)
    return out


@functools.lru_cache(maxsize=32)
def _branches(repo_path, minute):
    """{branch: last commit epoch} for the remote branches the checkout has fetched (refs/remotes/origin)."""
    try:
        out = subprocess.run(["git", "-C", repo_path, "for-each-ref", "refs/remotes/origin",
                              "--format=%(refname:short) %(committerdate:iso-strict)"],
                             capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    res = {}
    for line in out.splitlines():
        name, _, when = line.rpartition(" ")
        if name.startswith("origin/") and name != "origin/HEAD":
            res[name[len("origin/"):]] = _iso_any(when)
    return res


@functools.lru_cache(maxsize=32)
def _mainline(repo_path, minute):
    """[(commit epoch, [files])] on the default branch over 14 days."""
    ref = "origin/HEAD"
    try:
        out = subprocess.run(["git", "-C", repo_path, "log", ref, "--since=14.days", "--name-only", "--format=@%cI"],
                             capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    res, cur = [], None
    for line in out.splitlines():
        if line.startswith("@"):
            cur = (_iso_any(line[1:]), [])
            res.append(cur)
        elif line.strip() and cur:
            cur[1].append(line.strip())
    return res


def attach_docs(works, org, now):
    """Repository evidence for each work, from the main checkouts: commits to its RFC file, the last commit on each
    remote branch its match names (an open PR's pushes, an external doer's work), and commits on the default
    branch touching its match paths (docs/works.md §3 Moving)."""
    import fnmatch
    paths = checkouts(org)
    minute = int(now // GIT_CACHE_S)
    for w in works:
        doc = w.get("doc") or {}
        if doc.get("repo") in paths and doc.get("path") and "doc_commits" not in w:
            w["doc_commits"] = _doc_commits(paths[doc["repo"]], minute).get(doc["path"], [])
        mt = w.get("match") or {}
        pglobs = collections.defaultdict(list)
        for g in mt.get("paths") or []:
            repo, _, glob = str(g).partition(":")
            if glob:
                pglobs[repo].append(glob)
        repos = [r for r in dict.fromkeys([doc.get("repo")] + list(pglobs)) if r in paths]
        branches = []
        for r in repos:
            for name, t in _branches(paths[r], minute).items():
                if any(fnmatch.fnmatch(name, g) for g in mt.get("branches") or []):
                    branches.append({"repo": r, "branch": name, "last": t})
        w["branches"] = sorted(branches, key=lambda x: -(x["last"] or 0))
        hits = []
        for r, globs in pglobs.items():
            if r in paths:
                hits += [t for t, files in _mainline(paths[r], minute)
                         if any(fnmatch.fnmatch(f, g) or fnmatch.fnmatch(f, g.rstrip("*").rstrip("/") + "/*") for f in files for g in globs)]
        w["path_commits"] = sorted(hits, reverse=True)
    return works


# ------------------------------------------------------------------ stages (docs/works.md §3 Stage; st-v0)

STAGES_VERSION = "st-v0"
STAGE_HEAD = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?(?:(stage|phase|part|step|wave|milestone)\s+([0-9]+[a-z]?)\b|([PTWES][0-9]+[a-z]?)\b)", re.I)
HYP_ROW = re.compile(r"^\|\s*(H[0-9]+[a-z]?)\b")
DONE_WORDS = re.compile(r"\b(closed|done|built|shipped|merged|implemented|refuted|confirmed|completed?|rolled out)\b", re.I)
DONE_BOLD = re.compile(r"\*\*\s*(closed|done|built|shipped|merged|implemented|refuted|confirmed|completed?)\b", re.I)
STATUS_DONE = re.compile(r"\b(?:phases?|stages?|parts?|steps?)\s+([0-9a-z ,]+?(?:and [0-9a-z]+)?)\s+(?:are|is|were|was)?\s*"
                         r"(?:built|done|merged|shipped|complete|implemented|live)", re.I)


def _cat_files(repo_path, specs):
    """{"<ref>:<path>": text or None} through one `git cat-file --batch` (one process for many files)."""
    specs = list(dict.fromkeys(specs))
    if not specs:
        return {}
    try:
        r = subprocess.run(["git", "-C", repo_path, "cat-file", "--batch"], input=("\n".join(specs) + "\n").encode(),
                           capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return {}
    out, data, i = {}, r.stdout, 0
    for spec in specs:
        nl = data.find(b"\n", i)
        if nl < 0:
            break
        head = data[i:nl].decode("utf-8", "replace").split()
        i = nl + 1
        if len(head) == 3 and head[1] == "blob":
            size = int(head[2])
            out[spec] = data[i:i + size].decode("utf-8", "replace")
            i += size + 1
        else:
            out[spec] = None
    return out


def doc_texts(works, org):
    """{work id: (text, ref)}: each work's RFC as on the remote default branch, else on one of its three newest
    matched branches (an RFC still in its PR). Two batched reads per repository."""
    paths = checkouts(org)
    want = collections.defaultdict(list)
    for w in works:
        doc = w.get("doc") or {}
        if doc.get("repo") in paths and doc.get("path") and w["kind"] == "rfc":
            want[doc["repo"]].append(w)
    res = {}
    for repo, ws in want.items():
        first = _cat_files(paths[repo], [f'origin/HEAD:{w["doc"]["path"]}' for w in ws])
        retry = {}
        for w in ws:
            t = first.get(f'origin/HEAD:{w["doc"]["path"]}')
            if t is not None:
                res[w["id"]] = (t, "origin/HEAD")
            else:
                retry[w["id"]] = [f'origin/{b["branch"]}:{w["doc"]["path"]}' for b in (w.get("branches") or []) if b["repo"] == repo][:3]
        second = _cat_files(paths[repo], [x for v in retry.values() for x in v])
        for wid, specs in retry.items():
            hit = next((x for x in specs if second.get(x) is not None), None)
            res[wid] = (second[hit], hit.split(":", 1)[0]) if hit else (None, None)
    return res


def _plain(t):
    return re.sub(r"\*\*|`|\[([^\]]*)\]\([^)]*\)", lambda m: m.group(1) or "", t or "").strip()


def _stage_token(kind, num, short):
    if short:
        return short.lower()
    return f"{kind.lower()}-{num.lower()}"


def stages_from_doc(text, status_line=None):
    """[{id, title, done, source}] from the RFC: stage-like headings (Stage/Phase/Part/Step N, P1, T7, S1 ...) and
    hypothesis rows (| H4a ... |). Done when its heading says so, its row carries a bold verdict (**Closed ...),
    or the Status line says "phases 0 and 1 are built"."""
    out, seen = [], set()
    for line in (text or "").splitlines():
        if line.startswith("#"):
            head = line.lstrip("#").strip()
            m = STAGE_HEAD.match(head)
            if not m:
                continue
            sid = _stage_token(m.group(1) or "", m.group(2) or "", m.group(3))
            if sid in seen:
                continue
            seen.add(sid)
            out.append({"id": sid, "title": _plain(head)[:120], "done": bool(DONE_WORDS.search(head)), "source": "heading"})
        else:
            m = HYP_ROW.match(line)
            if m and m.group(1).lower() not in seen:
                sid = m.group(1).lower()
                seen.add(sid)
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                out.append({"id": sid, "title": _plain(cells[1] if len(cells) > 1 else cells[0])[:120],
                            "done": bool(DONE_BOLD.search(line)), "source": "hypotheses table"})
    for m in STATUS_DONE.finditer(status_line or ""):
        nums = set(re.findall(r"[0-9]+[a-z]?", m.group(1)))
        for st in out:
            n = re.sub(r"^[a-z-]*?([0-9]+[a-z]?)$", r"\1", st["id"])
            if st["id"].split("-")[0] in ("stage", "phase", "part", "step") and n in nums:
                st["done"], st["source"] = True, st["source"] + " + status line"
    return out


def stage_tokens(sid):
    """The tokens PRs and branches use for a stage id: 't2-t5' -> t2..t5, 'v2-v3' -> v2, v3, 's1-bt' -> s1-bt;
    a purely numeric id ('1', '4-6') names nothing safely and gets no token."""
    sid = sid.lower()
    m = re.match(r"^([a-z]+)([0-9]+)-\1?([0-9]+)$", sid)
    if m and int(m.group(3)) - int(m.group(2)) < 20:
        return [f"{m.group(1)}{n}" for n in range(int(m.group(2)), int(m.group(3)) + 1)]
    if re.fullmatch(r"[0-9-]+", sid) or len(sid) < 2:
        return []
    return [sid]


def stage_evidence(stages, prs, branches):
    """For each stage: the matched PRs and branches that name it (P2, h4a, stage-1, t7-...)."""
    for st in stages:
        toks = stage_tokens(st["id"])
        rx = re.compile(r"(?:^|-)(?:" + "|".join(re.escape(t) for t in toks) + r")(?:-|$)") if toks else None
        st["prs"] = [p for p in prs if rx and rx.search(p["key"])]
        st["branches"] = [b for b in branches if rx and rx.search(_norm(b["branch"].rpartition("/")[2]))]
        idle = "open" if st.get("source") == "registry" else "not started"
        st["state"] = "done" if st.get("done") else ("moving" if st["prs"] or st["branches"] else idle)
    return stages


def stage_label(sid):
    """'stage-0' -> 'Stage 0', 'h4a' -> 'H4a', 'p2' -> 'P2'."""
    m = re.match(r"^(stage|phase|part|step|wave|milestone)-(.+)$", sid)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    return sid[:1].upper() + sid[1:]


def summarize_stages(stages):
    if not stages:
        return None
    done = sum(1 for s in stages if s["state"] == "done")
    moving = [stage_label(s["id"]) for s in stages if s["state"] == "moving"]
    return f"{done} of {len(stages)} done" + (f" · moving: {', '.join(moving[:4])}" if moving else "")


# ------------------------------------------------------------------ state

def _norm(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def evidence(model, rows, works, now, attention=None, issues=()):
    """Adds to each work: prs (matched merged PRs, newest first), open_prs (its open PRs, latest state each),
    last_turn, waits, asks_open, issues, last_progress, progress_7d, state. A PR of a registry work matches by the
    `works` of its record (of_github.match_works at import: one rule with docs/registry.md); a record without that
    field, or a derived work, by the match here; and a doer's PR that matches no work goes to the doer's one open work."""
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    attrib = of_model.pr_attribution(rows, positions, sessions)
    prs = []
    for r in rows:
        pl = r.get("payload") or {}
        if r.get("name") != "of.work_record" or pl.get("source") == "pr-link" or of_model.BOT_AUTHOR.search(str(pl.get("author") or "")):
            continue
        who = r.get("position") if r.get("position") in positions else attrib.get(of_model.pr_key(pl))
        prs.append({"repo": pl.get("repo"), "number": pl.get("number"), "title": pl.get("title"), "url": pl.get("url"),
                    "branch": pl.get("head_ref"), "merged_at": pl.get("merged_at") or r.get("ts"), "position": who,
                    "systems": pl.get("systems") or [], "paths": pl.get("paths") or [],
                    "works": pl["works"] if isinstance(pl.get("works"), list) else None,
                    "key": _norm(f"{pl.get('head_ref')} {pl.get('title')}")})
    # open PRs (of.pr_state, one event per change; the newest per PR is its state): pushes and reviews are progress
    latest = {}
    for r in rows:
        pl = r.get("payload") or {}
        if r.get("name") != "of.pr_state" or of_model.BOT_AUTHOR.search(str(pl.get("author") or "")):
            continue
        k = of_model.pr_key(pl)
        when = (str(pl.get("updated_at") or ""), str(r.get("ts") or ""))
        if k and (k not in latest or when >= latest[k][0]):
            latest[k] = (when, r)
    open_prs = []
    for _, r in latest.values():
        pl = r.get("payload") or {}
        if pl.get("state") != "open":
            continue   # closed without a merge (a merge arrives as of.work_record)
        acts = [epoch(x) for x in (pl.get("last_push_at"), pl.get("last_review_at")) if x]
        open_prs.append({"repo": pl.get("repo"), "number": pl.get("number"), "title": pl.get("title"), "url": pl.get("url"),
                         "branch": pl.get("head_ref"), "draft": bool(pl.get("draft")), "created_at": pl.get("created_at"),
                         "last_push_at": pl.get("last_push_at"), "last_review_at": pl.get("last_review_at"),
                         "activity": max([a for a in acts if a] or [0]) or None,
                         "position": pl.get("position") if pl.get("position") in positions else (
                             r.get("position") if r.get("position") in positions else attrib.get(of_model.pr_key(pl))),
                         "systems": pl.get("systems") or [], "paths": pl.get("paths") or [],
                         "works": pl["works"] if isinstance(pl.get("works"), list) else None,
                         "key": _norm(f"{pl.get('head_ref')} {pl.get('title')}")})
    last_turn = {}
    for r in rows:
        if r.get("name") in ("of.turn_end", "of.agent_message_out"):
            pos = of_model.position_of(r, positions, sessions)
            if pos:
                last_turn[pos] = max(last_turn.get(pos, 0), epoch(r.get("ts")) or 0)
    att = collections.defaultdict(list)
    # an ask declared with --work belongs to that work, whoever asked; one without it, to its asker's works
    ask_work = {r.get("event_id"): str((r.get("payload") or {})["work"]) for r in rows
                if r.get("name") == "of.agent_question" and (r.get("payload") or {}).get("work")}
    by_work = collections.defaultdict(list)
    for x in attention if attention is not None else of_measures.attention(model, rows, now):
        att[x["position"]].append(x)
        if ask_work.get(x.get("ask_id")):
            by_work[ask_work[x["ask_id"]]].append(x)
    import fnmatch

    def explicit(w, p):
        if str(w.get("source") or "").startswith("registry/"):
            if p.get("works") is not None:
                return w["id"] in p["works"]   # matched at import, by the registry's rules
            # an older record without the field: the same function the import uses (of_github.match_works)
            import of_github
            doc = w.get("doc") or {}
            raw = {"match": dict(w.get("match") or {}), "systems": w.get("systems") or [],
                   "doc": f'{doc["repo"]}:{doc["path"]}' if doc.get("repo") and doc.get("path") else doc.get("path")}
            return bool(of_github.match_works({w["id"]: raw}, p["repo"], p["number"], p["branch"], p["paths"], p["systems"]))
        mt = w.get("match") or {}
        repo = (w.get("doc") or {}).get("repo")
        if p["number"] in mt.get("prs", []) and (not repo or str(p["repo"] or "").rpartition("/")[2] == repo):
            return True
        if any(fnmatch.fnmatch(p["branch"] or "", g) for g in mt.get("branches", [])):
            return True
        prepo = str(p["repo"] or "").rpartition("/")[2]
        for g in mt.get("paths", []):   # '<repo>:<glob>' against the PR's changed paths (of import github, from be3fb39)
            r_, _, glob = str(g).partition(":")
            if glob and r_ == prepo and any(fnmatch.fnmatch(f, glob) or fnmatch.fnmatch(f, glob.rstrip("*").rstrip("/") + "/*")
                                            for f in p["paths"]):
                return True
        slug = _norm(w.get("slug"))
        return bool(slug and len(slug) >= 8 and slug in p["key"])
    claimed = set()
    for w in works:
        w["prs"] = [p for p in prs if explicit(w, p)]
        w["open_prs"] = [p for p in open_prs if explicit(w, p)]
        claimed |= {id(p) for p in w["prs"] + w["open_prs"]}
    # a doer's PR that no work claims by its match goes to the doer's work, when it has exactly one open work;
    # with several it is ambiguous and stays unmatched (the structure keeper adds a match)
    open_works = collections.Counter(d for w in works if w.get("status") not in ("finished", "parked") for d in w["doers"])
    for w in works:
        single = {d for d in w["doers"] if open_works[d] == 1}
        w["prs"] += [p for p in prs if id(p) not in claimed and p["position"] in single]
        w["open_prs"] += [p for p in open_prs if id(p) not in claimed and p["position"] in single]
        w["ambiguous_doers"] = sorted(d for d in w["doers"] if open_works[d] > 1)
        w["prs"].sort(key=lambda p: epoch(p["merged_at"]) or 0, reverse=True)
        w["open_prs"].sort(key=lambda p: p["activity"] or 0, reverse=True)
    for w in works:
        doers = set(w["doers"])
        mine = w["prs"]
        w["last_turn"] = max((last_turn.get(d, 0) for d in doers), default=0) or None
        w["waits"] = sorted([x for d in doers for x in att.get(d, []) if x["kind"] != "stalled"
                             and ask_work.get(x.get("ask_id"), w["id"]) == w["id"]]
                            + [x for x in by_work.get(w["id"], []) if x["position"] not in doers],
                            key=lambda x: x.get("since") or "")
        w["stalled"] = [x for d in doers for x in att.get(d, []) if x["kind"] == "stalled"]
        w["issues"] = [i for i in issues if i.get("state") == "open" and (set(i.get("affected") or []) & (doers | set(w["systems"])))]
        marks = ([epoch(p["merged_at"]) or 0 for p in mine[:1]] + (w.get("doc_commits") or [])[:1]
                 + ([w["last_turn"]] if w["last_turn"] else []) + [b["last"] or 0 for b in (w.get("branches") or [])[:1]]
                 + (w.get("path_commits") or [])[:1] + [p["activity"] for p in w["open_prs"][:1] if p["activity"]])
        w["last_progress"] = max(marks) if marks else None
        week = now - 7 * DAY
        w["progress_7d"] = (sum(1 for p in mine if (epoch(p["merged_at"]) or 0) >= week)
                            + sum(1 for t in w.get("doc_commits") or [] if t >= week)
                            + sum(1 for b in w.get("branches") or [] if (b["last"] or 0) >= week)
                            + sum(1 for t in w.get("path_commits") or [] if t >= week)
                            + sum(1 for p in w["open_prs"] if (p["activity"] or 0) >= week))
        w["state"] = state(w, now)
        review = epoch(w["review"]) if w.get("review") else None
        w["past_review"] = bool(review and now > review + DAY and (w["last_progress"] or 0) < review + DAY)
        if w.get("standing"):
            standing(w, loop_card(model, rows, w, now), now)
    return works


def loop_card(model, rows, w, now):
    """The work's loop projection: the model's (one computation for the card and the rules), else, for a work
    listed outside positions.yaml, of_loops.project on the same rows."""
    card = (model.get("loops") or {}).get(w["id"])
    if w.get("loop") and not (card or {}).get("declared"):   # the model saw the events but not this list's spec
        import of_loops
        card = of_loops.project(w["id"], w["loop"], rows, now, work=w)
    return card


def standing(w, card, now):
    """A standing work (docs/registry.md): never derived as done, so its progress is the loop target's trend by
    checkpoint, not stages; it is past review only when its last loop decision (else the loop's start) is older
    than checkpoint_days. With no loop events at all, its `review` date decides, as for any work."""
    import of_loops
    w["trend"] = (card or {}).get("trend")
    w["trend_summary"] = of_loops.trend_text(card)
    decided = [x["ts"] for x in (card or {}).get("decisions_all") or [] if x.get("ts")]
    last = max(decided) if decided else (card or {}).get("start")
    w["last_decision"] = max(decided) if decided else None
    if last:
        w["past_review"] = now - last > ((card or {}).get("checkpoint_days") or 3) * DAY


def state(w, now):
    """docs/works.md §3: done/parked from the registry; waiting when a doer waits; active with progress in 3 days;
    proposed for an RFC nobody has touched; else quiet. `review` (last stage done) needs stages: not in v0. Nothing
    here derives done: only the keeper's `state: done` ends a work, a standing one above all."""
    if w.get("status") == "finished":
        return "done"
    if w.get("status") == "parked":
        return "parked"
    if w.get("waits"):
        return "waiting"
    if w.get("last_progress") and now - w["last_progress"] <= ACTIVE_DAYS * DAY:
        return "active"
    if w["kind"] == "rfc" and not w["doers"] and not w.get("prs") and re.match(r"\W*(proposal|proposed|draft)", w.get("status_line") or "", re.I):
        return "proposed"
    return "quiet"


ORDER = {"waiting": 0, "active": 1, "quiet": 2, "proposed": 3, "parked": 4, "done": 5}


def works_list(model, org, now):
    """The registry's confirmed works when there are any, else the derived list (v0)."""
    reg = registered(org)
    return attach_docs(reg, org, now) if reg is not None else derive(model, org, now)


def attach_stages(works, org, now):
    """Stages per work: the registry's `stages` when listed, else st-v0 from the RFC text; then evidence."""
    texts = doc_texts([w for w in works if w.get("state") not in ("done", "parked")], org)
    for w in works:
        if w.get("state") in ("done", "parked"):
            w["stages"], w["stage_summary"] = [], None
            continue
        if w.get("standing"):   # its progress is the loop's trend by checkpoint, never stages done
            w["stages"], w["stage_summary"] = [], None
            continue
        if w.get("stages") and isinstance(w["stages"], list) and isinstance(w["stages"][0], dict):
            st = [{"id": str(x.get("id") or i + 1).lower(), "title": x.get("title") or x.get("done_when") or "",
                   "done_when": x.get("done_when"), "note": x.get("note"),
                   "done": str(x.get("state") or "").lower() == "done", "source": "registry"} for i, x in enumerate(w["stages"])]
            w["stages_from"] = "registry"
        elif w["kind"] == "rfc":
            text, ref = texts.get(w["id"], (None, None))
            st = stages_from_doc(text, w.get("status_line_doc") or _status_of(text)) if text else []
            w["stages_from"] = f"{STAGES_VERSION} from {ref}" if text else None
            w["doc_found"] = text is not None
        else:
            st = []
        w["stages"] = stage_evidence(st, (w.get("prs") or []) + (w.get("open_prs") or []), w.get("branches") or [])
        w["stage_summary"] = summarize_stages(w["stages"])
    return works


def _status_of(text):
    for line in (text or "").splitlines()[:40]:
        m = re.match(r"\**status\**:?\**\s*(.*)", line.strip(), re.I)
        if m and m.group(1):
            return m.group(1)
    return None


def rows_for(model, rows, org, now, attention=None, issues=()):
    works = attach_stages(evidence(model, rows, works_list(model, org, now), now, attention, issues), org, now)
    on_owner = waits_on_owner
    return sorted(works, key=lambda w: (not on_owner(w), not w["past_review"], ORDER.get(w["state"], 9),
                                        -(w["last_progress"] or 0), w["id"]))


MAX_SIBLINGS = 7   # docs/registry.md, Goals: more than 7 active goals at one level is a warning


def _rollup(works):
    """What a set of works adds up to on a goal or a condition: the open ones, their doers, open asks, progress."""
    live = [w for w in works if w.get("state") not in ("done", "parked")]
    waits = {(x["position"], x.get("ask_id") or x.get("since")): x for w in live for x in w.get("waits") or []}
    return {"works": len(works), "open": len(live), "doers": sorted({d for w in live for d in w["doers"]}),
            "asks": len(waits), "on_owner": sum(1 for x in waits.values() if x["kind"] == "waiting_owner"),
            "progress_7d": sum(w.get("progress_7d") or 0 for w in live),
            "last_progress": max((w.get("last_progress") or 0 for w in live), default=0) or None,
            "states": dict(collections.Counter(w.get("state") or "quiet" for w in works)),
            "past_review": sum(1 for w in live if w.get("past_review"))}


def goal_tree(model, works, keep=None):
    """The goal tree (docs/registry.md, Goals): each goal in rank order (of_goals.tree) with its sub-goals, the works that name it, and
    the rollup of its whole branch (_rollup); the conditions beside it; the works that name neither apart. `keep`, a
    set of positions for a unit's view: only the branches with a work of theirs or a goal they own, with their
    ancestors. A goal no root reaches (a cycle of parents) is shown as a root with a note."""
    goals, conds = model.get("goals") or {}, model.get("conditions") or {}
    on_goal, on_cond, loose, unknown = collections.defaultdict(list), collections.defaultdict(list), [], []
    for w in works:
        if w.get("goal") in goals:
            on_goal[w["goal"]].append(w)
        elif w.get("condition") in conds:
            on_cond[w["condition"]].append(w)
        else:
            loose.append(w)
            if w.get("goal") or w.get("condition"):
                unknown.append((w["id"], w.get("goal") or w.get("condition")))
    import of_goals
    kids = collections.defaultdict(list)
    for gid, g in goals.items():
        kids[g["parent"]].append(gid)
    pos = {gid: i for i, (_, gid, _) in enumerate(of_goals.tree(goals))}   # one order with of registry check and of works
    order = lambda gid: (pos.get(gid, 1 << 30), gid)
    seen = set()

    def node(gid, depth, closed_above):
        seen.add(gid)
        g = goals[gid]
        children = [node(c, depth + 1, closed_above or g["state"] != "active") for c in sorted(kids[gid], key=order) if c not in seen]
        mine = sorted(on_goal.get(gid, []), key=lambda w: (ORDER.get(w.get("state"), 9), -(w.get("last_progress") or 0), w["id"]))
        branch = mine + [w for c in children for w in c["branch"]]
        notes = []
        if mine and children:
            notes.append(f"{len(mine)} work{'s' if len(mine) != 1 else ''} on a goal with sub-goals: each belongs on the sub-goal it moves")
        if closed_above and g["state"] == "active":
            notes.append("active under a goal that is achieved or dropped")
        n_active = sum(1 for c in children if c["state"] == "active")
        if n_active > MAX_SIBLINGS:
            notes.append(f"{n_active} active sub-goals: more than {MAX_SIBLINGS} is a list, not a choice")
        return dict(g, id=gid, depth=depth, children=children, works=mine, branch=branch, rollup=_rollup(branch), notes=notes,
                    leaf=not children)

    roots = [node(gid, 0, False) for gid in sorted(kids[None], key=order)]
    for gid in sorted(goals, key=order):   # a cycle of parents: no root reaches it
        if gid not in seen:
            n = node(gid, 0, False)
            n["notes"].insert(0, "its parents form a cycle: no root goal reaches it")
            roots.append(n)

    def wanted(n):
        n["children"] = [c for c in n["children"] if wanted(c)]
        return n["owner"] in keep or any(set(w["doers"]) & keep for w in n["works"]) or bool(n["children"])
    if keep is not None:
        keep = set(keep)
        roots = [n for n in roots if wanted(n)]
    conditions = [dict(c, id=cid, works=on_cond.get(cid, []), rollup=_rollup(on_cond.get(cid, [])))
                  for cid, c in sorted(conds.items(), key=lambda kv: (kv[1]["state"] != "active", kv[0]))
                  if keep is None or c["owner"] in keep or any(set(w["doers"]) & keep for w in on_cond.get(cid, []))]
    n_roots = sum(1 for n in roots if n["state"] == "active" and n["depth"] == 0)
    return {"roots": roots, "conditions": conditions, "loose": loose, "unknown": unknown,
            "notes": [f"{n_roots} active root goals: more than {MAX_SIBLINGS} is a list, not a choice"] if n_roots > MAX_SIBLINGS else []}


def waits_on_owner(w, who=of_model.HUMAN):
    return any(x["kind"] == "waiting_owner" and x.get("on") in (who, None, "owner") for x in w.get("waits") or [])


def group_of(model, w):
    """(unit, system) for display: the work's first system, its unit; an RFC with no doers by its repository."""
    systems = model["systems"]
    sid = next((s for s in w["systems"] if s in systems), None)
    if sid is None:
        return ("", (w.get("doc") or {}).get("repo") or "")
    unit = next((uid for uid, u in (model.get("org_units") or {}).items() if sid in (u.get("systems") or [])), "")
    return (unit, sid)


def age(t, now):
    if not t:
        return None
    s = max(0.0, now - t)
    return f"{int(s // 60)} min" if s < 3600 else (f"{s / 3600:.1f} h" if s < 2 * DAY else f"{int(s // DAY)} d")


def now_s():
    return time.time()
