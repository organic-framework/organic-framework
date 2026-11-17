"""`of protected ff <entry> --to <commit>`: an agent fast-forwards a protected worktree to a reviewed commit, and the
owner keeps only the fast-forwards that change what can reach the organization's secret (docs/guard.md G21).

The founder asked why agents ask him to fast-forward such a tree, and whether they could do it themselves (a user
organization's structure keeper, msg 01a1ea84-a0f4; its unit owner and its security keeper said yes to this design,
msg 01a1ea97-b59d). A `protected_code:` entry that is a mapping with `ff:` (docs/registry.md, Protected code) names the
branch the target must be on and its human set: paths in the tree, and lists in the tree that name more paths, read
at the worktree's HEAD and at the target and united so that a fast-forward cannot shrink its own set. Every
`.gitattributes` and the lists themselves always count.

The command moves only when:
- the target, given by its commit id, is on origin/<branch> and fast-forwards HEAD;
- GitHub shows it as a merged PR's merge commit into <branch>, with every required check green (the session's own
  GitHub access);
- the tree has no change of its own;
- no human path changed between HEAD and the target.
Then it checks the target out by its id. The system's git runs it, with fsmonitor, hooks, lazy fetch and every
transport off: no program the repository names runs. It checks that every human file on disk is the target's byte for
byte, moving back otherwise, and records `of.protected_ff`.

The guard's G21 still denies every git write into the tree from an agent's command line; this command is the one way
agents move such a tree. On one OS user it is a convention, not a boundary (G21's known limit): any program an agent
starts can write there. The boundary is the tree owned by another user, with a sudo rule that runs only this command.
"""

import json
import os
import re
import subprocess

SYSTEM_GIT = "/usr/bin/git"
SHA = re.compile(r"[0-9a-f]{7,40}")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")


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
        return ["ff must be a mapping with branch, human and human_lists"]
    out = [f"ff: unknown key {k!r}" for k in ff if k not in ("branch", "human", "human_lists", "checks")]
    if "checks" in ff and (not isinstance(ff["checks"], list) or not all(isinstance(c, str) and c.strip()
                                                                          for c in ff["checks"])):
        out.append("ff: checks must be a list of the required checks' names")
    if not isinstance(ff.get("branch"), str) or not BRANCH.fullmatch(ff["branch"]) or ".." in ff["branch"]:
        out.append("ff: branch must name the remote branch the target must be on, such as main")
    for key in ("human", "human_lists"):
        vals = ff.get(key, [])
        if not isinstance(vals, list) or not all(_rel_ok(v) for v in vals):
            out.append(f"ff: {key} must be a list of paths relative to the tree, with no .. and no leading /")
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


def _git(tree, *args, check=True, network=False, env=None):
    """The system's git in TREE: no git found on PATH, no program the repository names (fsmonitor, hooks, an external
    diff or textconv), and, unless NETWORK, no lazy fetch and no transport."""
    if not os.path.isfile(SYSTEM_GIT):
        raise Refused("no system git (/usr/bin/git) on this machine")
    base = {"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/"), "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1"}
    if network:  # a fetch reaches the remote with the session's own credentials and helpers (it runs as the session)
        base.update({k: v for k, v in os.environ.items() if k.startswith(("SSH_", "GIT_SSH", "GH_", "GITHUB_"))})
        base["PATH"] = os.environ.get("PATH", base["PATH"])
    else:
        base["GIT_NO_LAZY_FETCH"] = "1"
    cmd = [SYSTEM_GIT, "-C", tree, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
           "-c", "diff.external=", "-c", "core.pager=cat"] + ([] if network else ["-c", "protocol.allow=never"])
    r = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=120 if network else 30,
                       env=dict(base, **(env or {})))
    if check and r.returncode != 0:
        raise Refused(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:200]}")
    return r


def _blob(tree, rev, path):
    """The bytes of PATH at REV, or None when it is not there (no filter, no textconv)."""
    r = subprocess.run([SYSTEM_GIT, "-C", tree, "-c", "core.fsmonitor=false", "-c", "protocol.allow=never",
                        "cat-file", "blob", f"{rev}:{path}"], capture_output=True, timeout=30,
                       env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/"), "GIT_NO_LAZY_FETCH": "1",
                            "GIT_CONFIG_NOSYSTEM": "1"})
    return r.stdout if r.returncode == 0 else None


def _listed(text):
    """The relative paths a list file names: every word with a / in it, normalized (fails closed: a word that is not a
    path only adds a name that never changes; a path that leaves the tree or is absolute names nothing in it)."""
    import posixpath
    out = set()
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0]
        for w in re.split(r"[\s,;|]+", line):
            w = w.strip().strip("'\"")
            if "/" not in w or w.startswith("/"):
                continue
            n = posixpath.normpath(w)
            if n != ".." and not n.startswith("../"):
                out.add(n)
    return out


def human_set(tree, ff, revs):
    """The human paths: ff.human, ff.human_lists, and what the lists name at each of REVS (HEAD and the target). A list
    that cannot be read at either is no answer, so the fast-forward is the owner's (fails closed; of_architect on the
    field, msg 01a1eaa7-2645)."""
    paths = set(ff.get("human") or []) | set(ff.get("human_lists") or [])
    for lst in ff.get("human_lists") or []:
        for rev in revs:
            data = _blob(tree, rev, lst)
            if data is None:
                raise Refused(f"the list {lst} cannot be read at {rev[:12]}, so the human set is not known: the owner's")
            paths |= _listed(data.decode("utf-8", "replace"))
    return paths


def _covered(path, human):
    return path in human or any(h.endswith("/") and path.startswith(h) for h in human)


def _hits(changes, human, link_target=None):
    """The changes the owner keeps: a human path's change of any kind (content, mode, deletion; renames are read as a
    deletion and an addition), one under a listed folder (ending in /), every .gitattributes and .gitmodules, any
    submodule pointer, and a symbolic link, added or changed anywhere, that points at a human path (of_architect, msg
    01a1eaa7-2645). CHANGES: [(path, old mode, new mode)]; LINK_TARGET(path) the link's target as stored."""
    import posixpath
    out = []
    for path, old, new in changes:
        hit = _covered(path, human) or os.path.basename(path) in (".gitattributes", ".gitmodules") \
            or "160000" in (old, new)
        if not hit and new == "120000" and link_target:
            t = link_target(path)
            if t is None:
                hit = True  # a link whose target cannot be read is not known to be harmless
            else:
                dest = posixpath.normpath(posixpath.join(posixpath.dirname(path), t)) if not t.startswith("/") else t
                hit = dest.startswith("/") or dest.startswith("../") or _covered(dest, human) or \
                    any(h.startswith(dest.rstrip("/") + "/") for h in human)  # a link to a folder holding one
        if hit:
            out.append(path)
    return sorted(set(out))


def _raw_changes(tree, head, to):
    """[(path, old mode, new mode)] between HEAD and the target, renames read as a deletion and an addition."""
    raw = _git(tree, "diff", "--raw", "--no-renames", "--no-ext-diff", "--no-textconv", "-z", head, to).stdout
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


def _github_repo(tree):
    url = _git(tree, "config", "--get", "remote.origin.url").stdout.strip()
    m = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise Refused("its origin is not a GitHub repository, so the PR and its checks cannot be read")
    return m.group(1), m.group(2)


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


def github_proof(owner, repo, sha, branch, api=gh_api, checks=None):
    """The PR whose merge commit SHA is, merged into BRANCH, with every required check green. Returns the PR number,
    or raises Refused with what is missing. The required checks are the branch protection's when this access may read
    it, and those the registry's ff names (CHECKS); with none known it refuses (fails closed)."""
    st, pulls = api(f"repos/{owner}/{repo}/commits/{sha}/pulls")
    if st != 200:
        raise Refused(f"GitHub did not answer for {sha[:12]}'s PR, so it is not known to be merged: the owner's")
    pr = next((p for p in pulls or [] if isinstance(p, dict) and p.get("merged_at")
               and (p.get("base") or {}).get("ref") == branch and p.get("merge_commit_sha") == sha), None)
    if not pr:
        raise Refused(f"{sha[:12]} is no merged PR's merge commit into {branch} on GitHub")
    st, runs = api(f"repos/{owner}/{repo}/commits/{sha}/check-runs?per_page=100")
    st2, status = api(f"repos/{owner}/{repo}/commits/{sha}/status")
    green = {r.get("name"): r.get("conclusion") in ("success", "neutral", "skipped")
             for r in (runs or {}).get("check_runs") or [] if r.get("status") == "completed"}
    pending = [r.get("name") for r in (runs or {}).get("check_runs") or [] if r.get("status") != "completed"]
    for s in (status or {}).get("statuses") or []:
        green[s.get("context")] = green.get(s.get("context"), True) and s.get("state") == "success"
    st3, req = api(f"repos/{owner}/{repo}/branches/{branch}/protection/required_status_checks")
    required = set(checks or [])
    if st3 == 200 and isinstance(req, dict):
        required |= set(req.get("contexts") or []) | {c.get("context") for c in req.get("checks") or [] if c.get("context")}
    if not required:  # fails closed: with no required check known, no check vouches for the commit (of_architect)
        raise Refused(f"the required checks are not known (branch protection unreadable here, and the registry's ff "
                      f"names no checks), so nothing vouches for {sha[:12]}: the owner's")
    missing = sorted(c for c in required if not green.get(c))
    if missing:
        raise Refused(f"required checks not green on {sha[:12]}: {', '.join(missing)}")
    return pr.get("number")


def ff(tree, ff_spec, target, api=gh_api, fetch=True, log=print):
    """Fast-forward TREE to TARGET under FF_SPEC; returns {from, to, pr, changed}. Raises Refused, the tree untouched,
    when any check fails or a human path changed."""
    if not SHA.fullmatch(target or ""):
        raise Refused("give the target as a commit id, not a branch or a name, so nothing moves between check and use")
    branch = ff_spec["branch"]
    if fetch:
        _git(tree, "fetch", "--no-tags", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", network=True)
    to = _git(tree, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}").stdout.strip()
    head = _git(tree, "rev-parse", "--verify", "HEAD").stdout.strip()
    if to == head:
        raise Refused(f"the tree is at {to[:12]} already")
    if _git(tree, "merge-base", "--is-ancestor", to, f"refs/remotes/origin/{branch}", check=False).returncode != 0:
        raise Refused(f"{to[:12]} is not on origin/{branch}")
    if _git(tree, "merge-base", "--is-ancestor", head, to, check=False).returncode != 0:
        raise Refused(f"{to[:12]} does not fast-forward the tree's {head[:12]}")
    if _git(tree, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise Refused("the tree has changes of its own: someone wrote it; the owner looks before anything moves")
    changes = _raw_changes(tree, head, to)
    human = human_set(tree, ff_spec, (head, to))

    def link_target(path):
        data = _blob(tree, to, path)
        return None if data is None else data.decode("utf-8", "replace")
    hits = _hits(changes, human, link_target)
    if hits:
        raise Refused("it changes what can reach the secret, so this fast-forward stays the owner's: " + ", ".join(hits))
    owner, repo = _github_repo(tree)
    pr = github_proof(owner, repo, to, branch, api=api, checks=ff_spec.get("checks"))
    _git(tree, "checkout", "--quiet", "--detach", to)
    for path in sorted(human):  # on disk, byte for byte the target's: a smudge filter from config changes nothing
        full = os.path.join(tree, path)
        want = _blob(tree, to, path)
        if want is None or not os.path.isfile(full):
            continue
        with open(full, "rb") as fh:
            if fh.read() != want:
                _git(tree, "checkout", "--quiet", "--detach", head)
                raise Refused(f"{path} on disk is not the target's after the checkout (a filter?); moved back to "
                              f"{head[:12]}")
    return {"from": head, "to": to, "pr": pr, "changed": len(changes), "human": sorted(human),
            "human_lists": list(ff_spec.get("human_lists") or [])}


def run(entry, target, position=None, orgs_file=None, api=gh_api, fetch=True, emit=None):
    """`of protected ff`: find ENTRY among the registries' protected_code entries with ff, move it, record it."""
    import of_events
    want = os.path.normpath(os.path.expanduser(entry))
    found = next(((o, p, f) for o, p, f in entries(orgs_file) if os.path.realpath(p) == os.path.realpath(want)), None)
    if not found:
        raise Refused(f"{entry} is no protected_code entry with ff: in any registry here (docs/registry.md, Protected "
                      "code): its fast-forward is the owner's")
    org, path, spec = found
    res = ff(path, spec, target, api=api, fetch=fetch)
    (emit or of_events.emit)("protected_ff", {"entry": path.replace(os.path.expanduser("~"), "~", 1), "from": res["from"],
                                              "to": res["to"], "pr": res["pr"], "changed": res["changed"],
                                              "branch": spec["branch"], "human": res["human"],  # what it rested on
                                              "human_lists": res["human_lists"]}, position=position, org=org)
    return res
