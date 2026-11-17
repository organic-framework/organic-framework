"""`of protected ff <entry> --to <commit>`: an agent fast-forwards a protected worktree to a reviewed commit, and the
owner keeps only the fast-forwards that change what can reach the organization's secret (docs/guard.md G21).

The founder asked why agents ask him to fast-forward such a tree, and whether they could do it themselves (a user
organization's structure keeper, msg 01a1ea84-a0f4; its unit owner and its security keeper said yes to this design,
msg 01a1ea97-b59d; of_architect's points, msg 01a1eaa7-2645). A `protected_code:` entry that is a mapping with `ff:`
(docs/registry.md, Protected code) names the GitHub repository that vouches, the branch the target must be on, the
required checks when the branch protection cannot be read, and its human set: paths in the tree, and lists in the tree
that name more paths, read at the worktree's HEAD and at the target and united so that a fast-forward cannot shrink its
own set.

The command moves only when:
- the target, given by its commit id and already in the repository (the session fetches it), fast-forwards HEAD;
- GitHub, for the registry's repository, shows it as a merged PR's merge commit into <branch> that the branch still
  holds, with every required check green (the session's own GitHub access);
- the tree has no change of its own, and its git folder and config belong to the user running the command;
- the target holds no two paths that differ only in case;
- no human path changed between HEAD and the target. The owner always keeps every .gitattributes and .gitmodules, a
  submodule pointer, the lists themselves, a human path's change of any kind, and a link that points at a human path.
Paths are compared without case (the Mac's volume ignores it). Any check that cannot be made refuses.

Then it checks the target out by its id. The system's git runs it, with fsmonitor, hooks, the external diff, every
filter driver the configuration names and the attributes file off, and no fetch: no program the repository's
configuration names runs. It checks that every human file, and every file under a human folder, is on disk the
target's byte for byte; otherwise it moves back, checks the same files against HEAD, and says whether the tree is as
it was. It records `of.protected_ff`.

The guard's G21 still denies every git write into the tree from an agent's command line; this command is the one way
agents move such a tree. On one OS user it is a convention, not a boundary (G21's known limit): any program an agent
starts can write there, and the registry that names `ff:` is written by agents, as `protected_code:` is. The boundary
is the tree owned by another user, with a sudo rule that runs only this command.
"""

import json
import os
import re
import subprocess

SYSTEM_GIT = "/usr/bin/git"
SHA = re.compile(r"[0-9a-f]{7,40}")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+")
ALWAYS = (".gitattributes", ".gitmodules")


class Refused(Exception):
    """The fast-forward is not this command's to make; the message says why (and who makes it)."""


def entry_path(item):
    """The path of a protected_code item: the string itself, or a mapping's `path`."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict) and isinstance(item.get("path"), str):
        return item["path"]
    return None


def ff_problems(item):
    """What is wrong with a protected_code mapping's `ff:` (for `of registry check`), [] when it is right or absent."""
    if not isinstance(item, dict) or "ff" not in item:
        return []
    ff = item["ff"]
    if not isinstance(ff, dict):
        return ["ff must be a mapping with repo, branch, human and human_lists"]
    out = [f"ff: unknown key {k!r}" for k in ff if k not in ("repo", "branch", "human", "human_lists", "checks")]
    if not isinstance(ff.get("repo"), str) or not REPO.fullmatch(ff["repo"]):
        out.append("ff: repo must name the GitHub repository that vouches, owner/name")
    if not isinstance(ff.get("branch"), str) or not BRANCH.fullmatch(ff["branch"]) or ".." in ff["branch"]:
        out.append("ff: branch must name the remote branch the target must be on, such as main")
    if "checks" in ff and (not isinstance(ff["checks"], list) or not all(isinstance(c, str) and c.strip()
                                                                          for c in ff["checks"])):
        out.append("ff: checks must be a list of the required checks' names")
    for key in ("human", "human_lists"):
        vals = ff.get(key, [])
        if not isinstance(vals, list) or not all(_rel_ok(v) for v in vals):
            out.append(f"ff: {key} must be a list of paths relative to the tree, with no .. and no leading /")
        elif any(re.search(r"[*?\[]", v) for v in vals):
            out.append(f"ff: {key} names paths, not globs (a glob would cover nothing)")
    if not ff.get("human") and not ff.get("human_lists"):
        out.append("ff: name the human set (human or human_lists): a tree with none would move whatever changed")
    return out


def _rel_ok(p):
    return isinstance(p, str) and p.strip() == p and p and not p.startswith("/") and ".." not in p.split("/") \
        and "\n" not in p


def entries(orgs_file=None):
    """[(org, absolute path, ff mapping)] of every protected_code entry that names `ff:` and has no problem."""
    import yaml
    import of_credentials
    out = []
    for org, reg in of_credentials._registries(orgs_file):
        try:
            with open(reg) as fh:
                doc = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        for item in doc.get("protected_code") or []:
            p = entry_path(item)
            if isinstance(item, dict) and item.get("ff") and p and of_credentials._code_path_ok(p) \
                    and not ff_problems(item):
                out.append((org, of_credentials._abs(p), item["ff"]))
    return out


def _env():
    """git's environment: no system or global configuration (the caller's HOME is not read), no replace objects (a
    `git replace` in the shared refs would show other content under the vouched commit id: of_architect on
    organic-framework PR 306), no lazy fetch, no prompt."""
    return {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0"}


def _git_cmd(tree, extra=()):
    """The system's git in TREE with every setting that keeps the repository's programs from running (_git, _blob)."""
    if not os.path.isfile(SYSTEM_GIT):
        raise Refused("no system git (/usr/bin/git) on this machine")
    return [SYSTEM_GIT, "-C", tree, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "diff.external=", "-c", "core.pager=cat", "-c", "protocol.allow=never", "-c", "gc.auto=0",
            "-c", "maintenance.auto=false", "-c", "diff.ignoreSubmodules=none", *extra]


def _git(tree, *args, check=True, extra=()):
    """The system's git in TREE, never one found on PATH, with no program the repository names: fsmonitor, hooks, an
    external diff, a pager, lazy fetch and every transport off. EXTRA: more `-c` settings (the filter drivers)."""
    r = subprocess.run(_git_cmd(tree, extra) + list(args), capture_output=True, text=True, timeout=30, env=_env())
    if check and r.returncode != 0:
        raise Refused(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:200]}")
    return r


def _blob(tree, rev, path):
    """The bytes of PATH at REV, or None when it is not there (no filter, no textconv)."""
    r = subprocess.run(_git_cmd(tree) + ["cat-file", "blob", f"{rev}:{path}"], capture_output=True, timeout=30,
                       env=_env())
    return r.stdout if r.returncode == 0 else None


def _no_filters(tree):
    """`-c` settings that turn off every filter driver the configuration names, and the attributes file, so a checkout
    runs no smudge or process command (of_secrets-review on organic-framework PR 306: a smudge driver set in the main
    repository's config and named in its info/attributes ran during the checkout)."""
    out = ["-c", "core.attributesFile=/dev/null"]
    r = _git(tree, "config", "--get-regexp", r"^filter\.", check=False)
    names = {m.group(1) for line in r.stdout.splitlines()
             for m in [re.match(r"filter\.(.+)\.(?:smudge|clean|process|required)\s", line + " ")] if m}
    for n in sorted(names):
        out += ["-c", f"filter.{n}.smudge=", "-c", f"filter.{n}.clean=", "-c", f"filter.{n}.process=",
                "-c", f"filter.{n}.required=false"]
    return out


def _owned(tree):
    """The repository's git folder and its config belong to the user running this, and no one else may write them:
    under a sudo rule, a config agents write would name the programs git runs as the tree's owner (of_secrets-review)."""
    common = _git(tree, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    for path in (common, os.path.join(common, "config")):
        try:
            st = os.stat(path)
        except OSError:
            raise Refused(f"{path} cannot be read")
        if st.st_uid != os.geteuid() or st.st_mode & 0o022:
            raise Refused(f"{path} belongs to another user or others may write it: its configuration is not this "
                          "user's to trust")


def _listed(text):
    """The relative paths a list file names, normalized: every word, every field between tabs, commas, semicolons or
    bars (a path with a space in it), and every line whole. It fails closed: a word that is not a path only adds a
    name that never changes; a path that leaves the tree or is absolute names nothing in it (of_review and
    of_architect on organic-framework PR 306: a top-level name, a folder's trailing / and a path with a space were
    dropped)."""
    out = set()
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        words = set(re.split(r"\s+", line)) | set(re.split(r"[\t,;|]+", line)) | {line}
        for w in words:
            n = _norm(w.strip().strip("'\""))
            if n:
                out.add(n)
    return out


def human_set(tree, ff, revs):
    """The human paths: ff.human, ff.human_lists, and what the lists name at each of REVS (HEAD and the target). A list
    that cannot be read at either is no answer, so the fast-forward is the owner's (fails closed; of_architect on the
    field, msg 01a1eaa7-2645)."""
    paths = {n for n in (_norm(p) for p in list(ff.get("human") or []) + list(ff.get("human_lists") or [])) if n}
    for lst in ff.get("human_lists") or []:
        for rev in revs:
            mode = _git(tree, "ls-tree", rev, "--", lst, check=False).stdout.split(" ", 1)[0]
            if mode == "120000":  # a link's blob is its target's name, not the list (of_architect on PR 306)
                raise Refused(f"the list {lst} is a link at {rev[:12]}: the owner's")
            data = _blob(tree, rev, lst)
            if data is None:
                raise Refused(f"the list {lst} cannot be read at {rev[:12]}, so the human set is not known: the owner's")
            paths |= _listed(data.decode("utf-8", "replace"))
    return paths


def _fold(path):
    """A path as the Mac's volume compares it: Unicode-normalized and without case (of_architect on PR 306: NFD/NFC
    and case variants are one file there)."""
    import unicodedata
    return unicodedata.normalize("NFC", path).casefold()


def _norm(p):
    """A relative path, normalized: no ./, no doubled or trailing /. None for one that leaves the tree or is absolute."""
    import posixpath
    if not p or p.startswith("/"):
        return None
    n = posixpath.normpath(p)
    return None if n in (".", "..") or n.startswith("../") else n


def _covered(path, human):
    """Is PATH a human path or under one? Without case or Unicode form, and any entry is itself and everything under
    it, with or without a trailing / (of_secrets-review and of_architect on organic-framework PR 306)."""
    p = _fold(path)
    for h in human:
        h = _fold(h.rstrip("/"))
        if p == h or p.startswith(h + "/"):
            return True
    return False


def _link_dest(path, link_target, links, hops=8):
    """Where a link at PATH points, inside the tree, following links of the target tree (LINKS) up to HOPS; '' for the
    tree's root, None when it leaves the tree or cannot be read."""
    import posixpath
    for _ in range(hops):
        t = link_target(path)
        if t is None or t.startswith("/"):
            return None
        dest = posixpath.normpath(posixpath.join(posixpath.dirname(path), t))
        if dest == ".":
            return ""
        if dest.startswith("../") or dest == "..":
            return None
        if _fold(dest) not in links:
            return dest
        path = links[_fold(dest)]
    return None


def _hits(changes, human, link_target=None, links=None):
    """The changes the owner keeps: a human path's change of any kind (content, mode, deletion; renames are read as a
    deletion and an addition), one under a human entry, every .gitattributes and .gitmodules, any submodule pointer,
    and a symbolic link, added or changed anywhere, that points at a human path (of_architect, msg 01a1eaa7-2645).
    CHANGES: [(path, old mode, new mode)]; LINK_TARGET(path) the link's target as stored."""
    import posixpath
    out = []
    for path, old, new in changes:
        hit = _covered(path, human) or _fold(os.path.basename(path)) in ALWAYS or "160000" in (old, new)
        if not hit and new == "120000" and link_target:
            dest = _link_dest(path, link_target, links or {})
            # a link out of the tree, to its root, to a human path, or to a folder holding one (of_architect on PR 306:
            # `x -> .` and chains of links)
            hit = dest is None or dest == "" or _covered(dest, human) or \
                any(_fold(h.rstrip("/")).startswith(_fold(dest) + "/") for h in human)
        if hit:
            out.append(path)
    return sorted(set(out))


def _raw_changes(tree, head, to):
    """[(path, old mode, new mode)] between HEAD and the target, renames read as a deletion and an addition."""
    raw = _git(tree, "diff-tree", "-r", "--raw", "--no-renames", "--ignore-submodules=none", "-z", head, to).stdout
    parts, out, i = raw.split("\0"), [], 0
    while i < len(parts) - 1:
        meta = parts[i]
        if not meta.startswith(":"):
            i += 1
            continue
        fields = meta[1:].split()
        out.append((parts[i + 1], fields[0], fields[1]))
        i += 2
    return out


def _tree_paths(tree, rev):
    return [p for p in _git(tree, "ls-tree", "-r", "-z", "--name-only", rev).stdout.split("\0") if p]


def _tree_links(tree, rev):
    """{folded path: path} of every symbolic link in REV's tree."""
    out = {}
    for item in _git(tree, "ls-tree", "-r", "-z", rev).stdout.split("\0"):
        meta, _, path = item.partition("\t")
        if path and meta.startswith("120000 "):
            out[_fold(path)] = path
    return out


def _trusted(path):
    """PATH (the organizations list or a registry, which name the human set) belongs to the user running this and no one
    else may write it; else Refused."""
    try:
        st = os.stat(path)
    except OSError:
        raise Refused(f"{path} cannot be read")
    if st.st_uid != os.geteuid() or st.st_mode & 0o022:
        raise Refused(f"{path} belongs to another user or others may write it: the human set it names is not this "
                      "user's to trust")


def _under_sudo():
    """Run through sudo (or as root): the boundary mode, which needs a registry and a gh the caller cannot write. Not
    built; until it is, the command refuses there (of_review on organic-framework PR 306: under sudo, the caller's HOME
    named the registry and the human set)."""
    return os.geteuid() == 0 or bool(os.environ.get("SUDO_UID"))


def _case_twins(paths):
    """Paths of one tree that differ only in case (one file on the Mac's volume), as 'a = b'."""
    seen, out = {}, []
    for p in paths:
        for part in _prefixes(p):
            k = _fold(part)
            if k in seen and seen[k] != part:
                out.append(f"{seen[k]} = {part}")
            seen.setdefault(k, part)
    return sorted(set(out))


def _prefixes(path):
    bits = path.split("/")
    return ["/".join(bits[:i]) for i in range(1, len(bits) + 1)]


def _differs(tree, rev, paths):
    """The PATHS whose bytes on disk are not REV's: a file missing where REV has one, or other content."""
    out = []
    for path in sorted(paths):
        want = _blob(tree, rev, path)
        full = os.path.join(tree, path)
        if want is None:
            continue
        try:
            with open(full, "rb") as fh:
                got = fh.read()
        except FileNotFoundError:
            if _sparse_out(tree, path):
                continue
            out.append(path)
            continue
        except OSError:
            out.append(path)
            continue
        if got != want:
            out.append(path)
    return out


def _sparse_out(tree, path):
    """Is PATH outside the tree's sparse checkout (so it is not on disk by design)?"""
    r = _git(tree, "ls-files", "-t", "--", path, check=False)
    return r.stdout.startswith("S ")


def gh_api(path):
    """GET a GitHub API path with the session's own `gh` (its GitHub access); (status, json or None)."""
    import shutil
    gh = shutil.which("gh")
    if not gh:
        raise Refused("no gh on PATH: the PR and its checks cannot be read")
    r = subprocess.run([gh, "api", "-H", "Accept: application/vnd.github+json", path], capture_output=True, text=True,
                       timeout=60)
    if r.returncode != 0:
        status = 404 if "404" in r.stderr else 403 if "403" in r.stderr else 1
        return status, None
    try:
        return 200, json.loads(r.stdout or "null")
    except ValueError:
        return 1, None


def github_proof(repo, sha, branch, api=gh_api, checks=None):
    """The PR whose merge commit SHA is, merged into BRANCH of REPO (owner/name, from the registry), that the branch
    still holds, with every required check green. Returns the PR number, or raises Refused with what is missing.
    The required checks are the branch protection's, with its app when it names one, when this access may read it,
    and those the registry's ff names (CHECKS); with none known it refuses (fails closed)."""
    st, pulls = api(f"repos/{repo}/commits/{sha}/pulls")
    if st != 200:
        raise Refused(f"GitHub did not answer for {sha[:12]}'s PR, so it is not known to be merged: the owner's")
    pr = next((p for p in pulls or [] if isinstance(p, dict) and p.get("merged_at")
               and (p.get("base") or {}).get("ref") == branch and p.get("merge_commit_sha") == sha), None)
    if not pr:
        raise Refused(f"{sha[:12]} is no merged PR's merge commit into {branch} on GitHub")
    st, cmp = api(f"repos/{repo}/compare/{sha}...{branch}")
    if st != 200 or (cmp or {}).get("status") not in ("ahead", "identical"):  # the branch is SHA or past it
        raise Refused(f"{repo}'s {branch} does not hold {sha[:12]} (GitHub's compare), so it is not known to be on it")
    st, runs = api(f"repos/{repo}/commits/{sha}/check-runs?per_page=100")
    green = {}
    # check runs alone, and only success: a commit status may be posted by anyone who can write the repository, the
    # agents' own access included, and a skipped or neutral check ran nothing (of_architect on PR 306)
    for r in (runs or {}).get("check_runs") or []:
        ok = r.get("status") == "completed" and r.get("conclusion") == "success"
        app = (r.get("app") or {}).get("id")
        green.setdefault((r.get("name"), None), True)
        green[(r.get("name"), None)] &= ok
        green.setdefault((r.get("name"), app), True)
        green[(r.get("name"), app)] &= ok
    st3, req = api(f"repos/{repo}/branches/{branch}/protection/required_status_checks")
    required = {(c, None) for c in checks or []}
    if st3 == 200 and isinstance(req, dict):
        named = {c.get("context"): c.get("app_id") for c in req.get("checks") or [] if c.get("context")}
        required |= {(c, named.get(c) if named.get(c) not in (None, -1) else None)
                     for c in set(req.get("contexts") or []) | set(named)}
    if not required:  # fails closed: with no required check known, no check vouches for the commit (of_architect)
        raise Refused(f"the required checks are not known (branch protection unreadable here, and the registry's ff "
                      f"names no checks), so nothing vouches for {sha[:12]}: the owner's")
    missing = sorted(f"{c}" + (f" (app {a})" if a else "") for c, a in required if not green.get((c, a)))
    if missing:
        raise Refused(f"required checks not green on {sha[:12]}: {', '.join(missing)}")
    return pr.get("number")


def _remote_repo(tree):
    url = _git(tree, "config", "--get", "remote.origin.url", check=False).stdout.strip()
    m = re.fullmatch(r"(?:https://(?:[^@/]+@)?github\.com/|ssh://git@github\.com/|git@github\.com:)([^/]+)/([^/]+?)"
                     r"(?:\.git)?/?", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


def ff(tree, ff_spec, target, api=gh_api, log=print):
    """Fast-forward TREE to TARGET under FF_SPEC, holding the tree's lock; returns {from, to, pr, changed, human,
    human_lists}. Raises Refused, with the tree as it was (or saying plainly that it is not), when any check fails or a
    human path changed."""
    import fcntl
    if not SHA.fullmatch(target or ""):
        raise Refused("give the target as a commit id, not a branch or a name, so nothing moves between check and use")
    gitdir = _git(tree, "rev-parse", "--path-format=absolute", "--git-dir").stdout.strip()
    with open(os.open(os.path.join(gitdir, "of-protected-ff.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)) as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Refused("another fast-forward of this tree is running")
        return _ff(tree, ff_spec, target, api)


def _ff(tree, ff_spec, target, api):
    if not SHA.fullmatch(target or ""):
        raise Refused("give the target as a commit id, not a branch or a name, so nothing moves between check and use")
    repo, branch = ff_spec["repo"], ff_spec["branch"]
    _owned(tree)
    remote = _remote_repo(tree)
    if not remote or remote.casefold() != repo.casefold():
        raise Refused(f"the tree's origin is {remote or 'not a GitHub repository'}, not {repo} as the registry names it")
    to = _git(tree, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}", check=False).stdout.strip()
    if not to:
        raise Refused(f"{target} is not in the repository: fetch it first (the command itself fetches nothing)")
    head = _git(tree, "rev-parse", "--verify", "HEAD").stdout.strip()
    if to == head:
        raise Refused(f"the tree is at {to[:12]} already")
    if _git(tree, "merge-base", "--is-ancestor", head, to, check=False).returncode != 0:
        raise Refused(f"{to[:12]} does not fast-forward the tree's {head[:12]}")
    off = _no_filters(tree)  # status runs a clean driver on a file whose stat changed (of_secrets-review on PR 306)
    if _git(tree, "status", "--porcelain", "--untracked-files=no", extra=off).stdout.strip():
        raise Refused("the tree has changes of its own: someone wrote it; the owner looks before anything moves")
    twins = _case_twins(_tree_paths(tree, to))
    if twins:
        raise Refused("the target holds paths that differ only in case, one file on this volume: " + "; ".join(twins))
    changes = _raw_changes(tree, head, to)
    human = human_set(tree, ff_spec, (head, to))

    def link_target(path):
        data = _blob(tree, to, path)
        return None if data is None else data.decode("utf-8", "replace")
    hits = _hits(changes, human, link_target, _tree_links(tree, to))
    if hits:
        raise Refused("it changes what can reach the secret, so this fast-forward stays the owner's: " + ", ".join(hits))
    pr = github_proof(repo, to, branch, api=api, checks=ff_spec.get("checks"))
    guarded = [p for p in set(_tree_paths(tree, to)) | set(_tree_paths(tree, head)) if _covered(p, human)]
    if _git(tree, "rev-parse", "--verify", "HEAD").stdout.strip() != head:  # moved while it was checked
        raise Refused("the tree moved while it was checked; run the command again")
    _git(tree, "checkout", "--quiet", "--detach", to, extra=off)
    bad = _differs(tree, to, guarded)
    if bad:  # on disk not the target's: move back and say whether the tree is as it was
        _git(tree, "checkout", "--quiet", "--force", "--detach", head, extra=off, check=False)
        still = _differs(tree, head, guarded)
        raise Refused(f"{', '.join(bad)} on disk was not the target's after the checkout; moved back to {head[:12]}"
                      + (f", but {', '.join(still)} still differ from it: the tree is not as it was, the owner must "
                         "look" if still else ", and the human files are HEAD's again"))
    return {"from": head, "to": to, "pr": pr, "changed": len(changes), "human": sorted(human),
            "human_lists": list(ff_spec.get("human_lists") or [])}


def run(entry, target, position=None, orgs_file=None, api=gh_api, emit=None):
    """`of protected ff`: find ENTRY among the registries' protected_code entries with ff, move it, record it."""
    import of_events
    import of_credentials
    if _under_sudo():
        raise Refused("run through sudo or as root: that mode needs a registry and a gh the caller cannot write, and is "
                      "not built yet")
    want = os.path.normpath(os.path.expanduser(entry))
    _trusted(of_credentials._orgs_file(orgs_file))
    found = next(((o, p, f) for o, p, f in entries(orgs_file) if os.path.realpath(p) == os.path.realpath(want)), None)
    if not found:
        raise Refused(f"{entry} is no protected_code entry with ff: in any registry here (docs/registry.md, Protected "
                      "code): its fast-forward is the owner's")
    org, path, spec = found
    _trusted(dict(of_credentials._registries(orgs_file))[org])
    res = ff(path, spec, target, api=api)
    (emit or of_events.emit)("protected_ff", {"entry": path.replace(os.path.expanduser("~"), "~", 1), "from": res["from"],
                                              "to": res["to"], "pr": res["pr"], "changed": res["changed"],
                                              "repo": spec["repo"], "branch": spec["branch"],
                                              "human": res["human"], "human_lists": res["human_lists"]},
                                             position=position, org=org)
    return res
