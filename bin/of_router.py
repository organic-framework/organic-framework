"""The mandate router, observe mode (RFC 0001 v3 §5.7; founder: route, don't block).

For an enrolled position, each Edit/Write/MultiEdit/NotebookEdit/Read/Grep/Glob/Bash call is
resolved against the organizations' systems maps: the path belongs to a system, and that system
has a keeper. The verdict:
  in_mandate        the system is the position's own (its `system`, or it keeps that system)
  owned_by:<pos>    another position keeps it
  unowned           a system without a keeper, or a file path no system claims
  unscoped          a Bash call whose working directory names no system (a repository root)
  outside           a path under no organization's root (a scratchpad, the harness's memory)
written as `of.mandate_check` {verdict, decision: admit, mode: observe}. Observe mode never
blocks and adds no context, so it runs on PostToolUse (no latency before the tool runs).
The systems index (compiled globs per organization root) is cached with the registry index.
"""

import os
import re

VERSION = "rt-v6"  # rt-v1: the first observe router; rt-v2: worktrees as their repository, Bash cwd unscoped;
#                    rt-v3: a path outside every organization (scratchpads, harness memory) is outside;
#                    rt-v4: a worktree resolves by its .git file (gitdir), whatever its folder is called;
#                    rt-v5: a keeper-less system that is not live (unsupported, retired) is non_live:<lifecycle>;
#                    rt-v6: a character class in a paths pattern ([a-f], [!x]) matches as in bin/of, not literally

PATH_TOOLS = {"Edit": "file_path", "Write": "file_path", "MultiEdit": "file_path", "NotebookEdit": "notebook_path",
              "Read": "file_path", "Grep": "path", "Glob": "path"}


def glob_rx(p):
    out, i = [], 0
    while i < len(p):
        if p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        elif p[i] == "[" and "]" in p[i + 1:]:  # as bin/of's glob_regex: [!x] and [^x] never match a slash
            j = p.index("]", i + 1)
            body = p[i + 1:j].replace("\\", "\\\\")
            out.append("[^" + body[1:] + "/]" if body.startswith(("!", "^")) else "[" + body + "]")
            i = j + 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    return "^" + "".join(out) + "$"


def build_index(positions, org_paths):
    """[[root, org, system, keeper, [regex...], lifecycle]] sorted so longer patterns win."""
    out = []
    for org, roots in org_paths.items():
        doc_systems = {}
        for p in positions:
            if p.get("_org") == org and p.get("_systems") is not None:
                doc_systems = p["_systems"]
                break
        for root in roots:
            root = os.path.realpath(os.path.expanduser(root))
            for sid, s in (doc_systems or {}).items():
                if not isinstance(s, dict):
                    continue
                pats = [glob_rx(str(x)) for x in s.get("paths") or []]
                pats += [glob_rx(f"{r}/**") for r in (s.get("repos") or []) if not s.get("paths")]
                if pats:
                    out.append([root, org, sid, s.get("keeper"), pats, s.get("lifecycle") or "live"])
    return out


WORKTREE = re.compile(r"(^|/)\.claude/worktrees/[^/]+(?=/|$)")


_WT_CACHE = {}


def worktree_main(path):
    """(worktree root, main repository root) when the path is inside a linked git worktree, else None. A linked
    worktree's `.git` is a file, 'gitdir: <repo>/.git/worktrees/<name>', which names the repository whatever the
    worktree's folder is called (<repo>-wt/<name>, <repo>-wt-<name>, .worktrees/<other name>; of_studio,
    2026-11-07). Cached per folder."""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    walked, res = [], None
    while d and d != os.path.dirname(d):
        if d in _WT_CACHE:
            res = _WT_CACHE[d]
            break
        walked.append(d)
        g = os.path.join(d, ".git")
        if os.path.isfile(g):
            try:
                with open(g) as fh:
                    m = re.match(r"gitdir:\s*(.+?)/\.git/worktrees/[^/]+/?$", fh.read(1024).strip())
            except OSError:
                m = None
            res = (d, os.path.realpath(m.group(1))) if m else None
            break
        if os.path.isdir(g):
            break  # a main checkout
        d = os.path.dirname(d)
    for w in walked:
        _WT_CACHE[w] = res
    return res


def unworktree(rel, repos=()):
    """A git worktree's path as its repository's: `storefront/.claude/worktrees/x/crates/a.rs` ->
    `storefront/crates/a.rs`; `.worktrees/devops-k8s-wazuh/k8s/a` -> `devops-k8s/k8s/a` (the longest
    known repository the worktree's name starts with)."""
    rel = WORKTREE.sub("", rel).lstrip("/")
    m = re.match(r"^\.worktrees/([^/]+)(/.*)?$", rel)
    if m:
        name = m.group(1)
        repo = max((r for r in repos if name == r or name.startswith(r + "-")), key=len, default=None)
        if repo:
            rel = repo + (m.group(2) or "")
        return rel
    # sibling worktree folders, by name, for worktrees already removed (the gitdir is the live answer)
    m = re.match(r"^([^/]+)-wt/[^/]+(/.*)?$", rel)
    if m and m.group(1) in repos:
        return m.group(1) + (m.group(2) or "")
    m = re.match(r"^([^/]+)(/.*)?$", rel)
    if m and "-wt-" in m.group(1):
        repo = max((r for r in repos if m.group(1).startswith(r + "-wt-")), key=len, default=None)
        if repo:
            return repo + (m.group(2) or "")
    return rel


def resolve(path, index):
    """(org, system, keeper) for an absolute path, or (None, None, None)."""
    full = os.path.realpath(path)
    wt = worktree_main(full)
    if wt:  # the same path in the main checkout
        full = wt[1] + full[len(wt[0]):]
    best, best_len = (None, None, None), -1
    repos = {rx.lstrip("^").split("/")[0].replace("\\", "") for e in index for rx in e[4]}
    for e in index:
        root, org, sid, keeper, pats = e[:5]
        if not (full == root or full.startswith(root + os.sep)):
            continue
        rel = unworktree(os.path.relpath(full, root), repos)
        for rx in pats:
            if re.match(rx, rel) and len(rx) > best_len:
                best, best_len = (org, sid, keeper), len(rx)
    return best


def lifecycle_of(index, org, system):
    """live | unsupported | retired for a system of the index (an index cached before rt-v5 says live)."""
    return next((e[5] for e in index if len(e) > 5 and e[1] == org and e[2] == system), "live")


def inside(path, index):
    """Is the path under any organization's root (its repositories)?"""
    full = os.path.realpath(path)
    return any(full == root or full.startswith(root + os.sep) for root, *_ in index)


def target_path(tool, tool_input, cwd):
    key = PATH_TOOLS.get(tool)
    if key:
        p = (tool_input or {}).get(key)
        if not p:
            return cwd if tool in ("Grep", "Glob") else None
        return p if os.path.isabs(p) else os.path.join(cwd or "", p)
    if tool == "Bash":
        return cwd
    return None


def verdict(position, position_system, system, keeper, tool=None, outside=False, lifecycle="live"):
    if system is None and outside:
        return "outside"  # not organization material: a scratchpad, the harness's own memory
    if system is None:
        # a shell's working directory (a repository root, a worktree root) names no file: not a
        # finding about ownership; only a file path no system claims is `unowned`
        return "unscoped" if tool == "Bash" else "unowned"
    if system == position_system or keeper == position:
        return "in_mandate"
    if keeper:
        return f"owned_by:{keeper}"
    if lifecycle and lifecycle != "live":
        return f"non_live:{lifecycle}"  # shown, not flagged: only a live system asks for a keeper (docs/registry.md)
    return "unowned"
