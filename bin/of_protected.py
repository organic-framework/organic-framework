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
- the target, given by its commit id and already in the repository (the session fetches it), fast-forwards HEAD, read
  from the commit objects the command hashes itself, never through grafts or the commit graph;
- GitHub, for the registry's repository, shows it as a merged PR's merge commit into <branch> that the branch still
  holds, with every required check green (the session's own GitHub access);
- the tree has no change of its own, and its git folder and config belong to the user running the command;
- the target holds no two paths that differ only in case;
- no human path changed between HEAD and the target. The owner always keeps every .gitattributes and .gitmodules, a
  submodule pointer, the lists themselves, a human path's change of any kind, and a link that points at a human path.
Paths are compared without case (the Mac's volume ignores it). Any check that cannot be made refuses.

It reads the commit and every tree of HEAD and of the target itself, from the root down, and hashes each, and every blob
either checkout would write (each changed path's, each human path's, every .gitattributes and .gitmodules), and holds
those bytes: git checks the root tree as it reads it, not the trees below it or the blobs (of_architect on
organic-framework PR 306). The changes, the lists, the links and the case check come from those verified trees. Then
it checks the target out by its id from a private object folder that holds only the verified objects, with no
alternates, so from the check on git reads no object from the repository's shared store, where a program on the same
OS user could swap one (of_architect's fourth pass). The system's git runs it, with fsmonitor, hooks, the external
diff, submodule recursion, sparse checkout, every filter driver the configuration names and the attributes file off,
and no fetch (a human path the index leaves out or marks unchanged is refused before); the
status before it never reads a submodule's work tree: no program the repository's configuration, or a submodule's
own, names runs. After the checkout the whole index must be the verified target, and every file it wrote and every
human file must hash on disk to the target's ids; otherwise it moves back from the same private folder, checks the
index and those files against HEAD's verified trees, and says whether the tree is as it was. It records
`of.protected_ff`.

The guard's G21 still denies every git write into the tree from an agent's command line; this command is the one way
agents move such a tree. On one OS user it is a convention, not a boundary (G21's known limit): any program an agent
starts can write there, and the registry that names `ff:` is written by agents, as `protected_code:` is. The boundary
is the tree owned by another user, with a sudo rule that runs only this command.
"""

import hashlib
import json
import os
import re
import stat
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
            "GIT_OPTIONAL_LOCKS": "0", "GIT_GRAFT_FILE": "/dev/null"}


def _git_cmd(tree, extra=()):
    """The system's git in TREE with every setting that keeps the repository's programs from running (_git, _blob)."""
    if not os.path.isfile(SYSTEM_GIT):
        raise Refused("no system git (/usr/bin/git) on this machine")
    return [SYSTEM_GIT, "-C", tree, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "diff.external=", "-c", "core.pager=cat", "-c", "protocol.allow=never", "-c", "gc.auto=0",
            "-c", "maintenance.auto=false", "-c", "diff.ignoreSubmodules=none", "-c", "submodule.recurse=false",
            "-c", "core.commitGraph=false",
            *extra]


def _git(tree, *args, check=True, extra=(), store=None):
    """The system's git in TREE, never one found on PATH, with no program the repository names: fsmonitor, hooks, an
    external diff, a pager, lazy fetch and every transport off. EXTRA: more `-c` settings (the filter drivers). STORE: a
    private object folder git reads instead of the repository's (and of no alternates)."""
    env = dict(_env(), GIT_OBJECT_DIRECTORY=store) if store else _env()
    r = subprocess.run(_git_cmd(tree, extra) + list(args), capture_output=True, text=True, timeout=30, env=env)
    if check and r.returncode != 0:
        raise Refused(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:200]}")
    return r


class _Snap(dict):
    """{path: (mode, id)} of every entry but folders in one commit, read and hashed by this command from the commit
    down. .fmt: the repository's object format; .rev: the commit; .objects: {id: (kind, bytes)} of the commit and every
    tree, as verified."""
    fmt = "sha1"
    rev = ""
    objects = None


FORGED = "these objects do not hash to their ids, so the content is not the reviewed commit's: "


def _hash(fmt, kind, data):
    h = hashlib.new("sha256" if fmt == "sha256" else "sha1")
    h.update(b"%s %d\0" % (kind, len(data)) + data)
    return h.hexdigest()


def _objects(tree, fmt, oids, kind):
    """{id: bytes} of the objects of KIND among OIDS whose content hashes to their id; any other (missing, of another
    kind, or content that is not its id's) is left out, for the caller to refuse."""
    oids = list(dict.fromkeys(oids))
    if not oids:
        return {}
    r = subprocess.run(_git_cmd(tree) + ["cat-file", "--batch"], input="".join(f"{o}\n" for o in oids).encode(),
                       capture_output=True, timeout=120, env=_env())
    data, pos, out = r.stdout, 0, {}
    for oid in oids:
        nl = data.find(b"\n", pos)
        if nl < 0:
            break
        head, pos = data[pos:nl].split(), nl + 1
        if len(head) != 3 or not head[2].isdigit():
            continue                                                    # "<id> missing" and the like
        size = int(head[2])
        body, pos = data[pos:pos + size], pos + size + 1
        if head[0].decode() == oid and head[1].decode() == kind and _hash(fmt, kind.encode(), body) == oid:
            out[oid] = body
    return out


def _mode(m):
    m = m.decode()
    if m in ("40000", "040000"):
        return "040000"
    if m in ("120000", "160000"):
        return m
    return "100755" if int(m, 8) & 0o111 else "100644"


def _snapshot(tree, commit):
    """COMMIT's entries as a _Snap, every object on the way (the commit and each tree) read here and hashed; a forged
    one is Refused (of_architect's third pass on organic-framework PR 306: a forged subtree under a genuine id)."""
    snap = _Snap()
    snap.rev, snap.objects = commit, {}
    snap.fmt = _git(tree, "rev-parse", "--show-object-format", check=False).stdout.strip() or "sha1"
    width = 32 if snap.fmt == "sha256" else 20
    raw = _objects(tree, snap.fmt, [commit], "commit").get(commit)
    m = re.match(rb"tree ([0-9a-f]+)\n", raw or b"")
    if not m:
        raise Refused(FORGED + f"the commit {commit[:12]}")
    snap.objects[commit] = ("commit", raw)
    level = {"": m.group(1).decode()}
    while level:
        got, nxt = _objects(tree, snap.fmt, list(level.values()), "tree"), {}
        for prefix, oid in level.items():
            if oid not in got:
                raise Refused(FORGED + f"the folder {prefix or '(the root)'} at {commit[:12]}")
            data, i = got[oid], 0
            snap.objects[oid] = ("tree", data)
            while i < len(data):
                sp = data.index(b" ", i)
                nul = data.index(b"\0", sp)
                name = data[sp + 1:nul].decode("utf-8", "surrogateescape")
                ent, mode, i = data[nul + 1:nul + 1 + width].hex(), _mode(data[i:sp]), nul + 1 + width
                if not name or name in (".", "..") or "/" in name or _fold(name) == ".git":
                    raise Refused(f"{commit[:12]} names a path git would refuse: {prefix}/{name!r}")
                path = f"{prefix}/{name}" if prefix else name
                if mode == "040000":
                    nxt[path] = ent
                else:
                    snap[path] = (mode, ent)
        level = nxt
    return snap


def _read(tree, snap, path):
    """The verified bytes of PATH in SNAP, or None when it holds no file there."""
    ent = snap.get(path)
    if ent is None or ent[0] == "160000":
        return None
    data = _objects(tree, snap.fmt, [ent[1]], "blob").get(ent[1])
    if data is None:
        raise Refused(FORGED + path)
    return data


def _held(tree, snaps, paths):
    """{id: bytes} of the blob at each of PATHS (and of every .gitattributes and .gitmodules, which git reads from the
    index) in each of SNAPS, read and hashed; a forged one is Refused, naming its paths."""
    want = {}
    for snap in snaps:
        for p, (mode, oid) in snap.items():
            if mode != "160000" and (p in paths or _fold(os.path.basename(p)) in ALWAYS):
                want.setdefault(oid, set()).add(p)
    got = _objects(tree, snaps[0].fmt, list(want), "blob")
    forged = sorted(p for oid, ps in want.items() if oid not in got for p in ps)
    if forged:
        raise Refused(FORGED + ", ".join(forged[:20]))
    return got


def _private_store(snaps, blobs):
    """A fresh object folder (mode 700) holding only the verified commits, trees and blobs, with no alternates: git
    reads it in place of the repository's shared store, so an object swapped there after the check is never read
    (of_architect's fourth pass on organic-framework PR 306). The caller removes it."""
    import tempfile
    import zlib
    d = tempfile.mkdtemp(prefix="of-protected-ff-")
    try:
        os.makedirs(os.path.join(d, "info"))
        os.makedirs(os.path.join(d, "pack"))
        objs = {oid: v for snap in snaps for oid, v in snap.objects.items()}
        objs.update({oid: ("blob", body) for oid, body in blobs.items()})
        for oid, (kind, body) in objs.items():
            os.makedirs(os.path.join(d, oid[:2]), exist_ok=True)
            fd = os.open(os.path.join(d, oid[:2], oid[2:]), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o400)
            with os.fdopen(fd, "wb") as fh:
                fh.write(zlib.compress(b"%s %d\0" % (kind.encode(), len(body)) + body))
    except BaseException:  # a store half written is removed here, not left behind (of_architect's fifth pass)
        import shutil
        shutil.rmtree(d, ignore_errors=True)
        raise
    return d


def _descends(tree, head, to, limit=20000):
    """Is HEAD an ancestor of TO? Read from commit objects this command hashes itself, never through grafts, replace
    refs or the commit graph (of_architect's fifth pass on organic-framework PR 306: a graft turned a rollback into a
    fast-forward). Refused when a commit cannot be read or does not hash, or past LIMIT commits."""
    fmt = _git(tree, "rev-parse", "--show-object-format", check=False).stdout.strip() or "sha1"
    seen, level = {to}, [to]
    while level:
        got, nxt = _objects(tree, fmt, level, "commit"), []
        for c in level:
            if c not in got:
                raise Refused(FORGED + f"the commit {c[:12]} (or it is not in the repository)")
            for line in got[c].split(b"\n\n", 1)[0].split(b"\n"):
                if line.startswith(b"parent "):
                    parent = line[7:].decode().strip()
                    if parent == head:
                        return True
                    if parent not in seen:
                        seen.add(parent)
                        nxt.append(parent)
        if len(seen) > limit:
            raise Refused(f"over {limit} commits read without reaching the tree's {head[:12]}: the owner's")
        level = nxt
    return False


def _unchecked(tree, human):
    """The human paths the index leaves out of the work tree (skip-worktree) or marks unchanged (assume-unchanged), so
    neither a checkout nor a status would look at them on disk (of_architect's fifth pass on PR 306)."""
    out = []
    for item in _git(tree, "ls-files", "-v", "-z").stdout.split("\0"):
        tag, _, path = item.partition(" ")
        if path and (tag in ("S", "s") or tag.islower()) and _covered(path, human):
            out.append(path)
    return sorted(out)


def _index_differs(tree, snap):
    """The paths where the tree's index is not SNAP (another id or mode, an entry missing or added, a conflict)."""
    seen, out = {}, []
    for item in _git(tree, "ls-files", "-s", "-z").stdout.split("\0"):
        meta, _, path = item.partition("\t")
        if not path:
            continue
        mode, oid, stage = (meta.split() + ["", "", ""])[:3]
        if stage != "0":
            out.append(path)
        seen[path] = (mode, oid)
    return sorted(set(out) | {p for p in set(seen) | set(snap) if seen.get(p) != snap.get(p)})


def _changes(old, new):
    """[(path, old mode, new mode)] between two _Snaps, a rename read as a deletion and an addition."""
    out = []
    for path in sorted(set(old) | set(new)):
        a, b = old.get(path), new.get(path)
        if a != b:
            out.append((path, a[0] if a else "000000", b[0] if b else "000000"))
    return out


def _no_filters(tree):
    """`-c` settings that turn off every filter driver the configuration names, and the attributes file, so a checkout
    runs no smudge or process command (of_secrets-review on organic-framework PR 306: a smudge driver set in the main
    repository's config and named in its info/attributes ran during the checkout)."""
    out = ["-c", "core.attributesFile=/dev/null"]
    # key names only, each split at its last dot: a value never shapes a name (of_architect on PR 306: a value holding
    # ` x.clean ` made the greedy match name another driver, and x's smudge ran)
    r = _git(tree, "config", "--null", "--name-only", "--get-regexp", r"^filter\.", check=False)
    names = {k[len("filter."):].rsplit(".", 1)[0] for k in r.stdout.split("\0")
             if k.startswith("filter.") and k.count(".") >= 2}
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
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        words = set(re.split(r"\s+", line)) | set(re.split(r"[\t,;|]+", line)) | {line}
        whole = raw.strip()  # a # inside a path is part of it: the line read whole as well (of_architect on PR 306)
        if whole and not whole.startswith("#"):
            words |= set(re.split(r"\s+", whole)) | set(re.split(r"[\t,;|]+", whole)) | {whole}
        for w in words:
            n = _norm(w.strip().strip("'\""))
            if n:
                out.add(n)
    return out


def human_set(tree, ff, snaps):
    """The human paths: ff.human, ff.human_lists, and what the lists name in each of SNAPS (HEAD's and the target's
    verified trees). A list that cannot be read in either is no answer, so the fast-forward is the owner's (fails
    closed; of_architect on the field, msg 01a1eaa7-2645)."""
    paths = {n for n in (_norm(p) for p in list(ff.get("human") or []) + list(ff.get("human_lists") or [])) if n}
    for lst in ff.get("human_lists") or []:
        for snap in snaps:
            if (snap.get(_norm(lst) or lst) or ("",))[0] == "120000":  # a link's blob is its target's name, not the list
                raise Refused(f"the list {lst} is a link at {snap.rev[:12]}: the owner's")
            data = _read(tree, snap, _norm(lst) or lst)
            if data is None:
                raise Refused(f"the list {lst} cannot be read at {snap.rev[:12]}, so the human set is not known: the "
                              "owner's")
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
        bits = dest.split("/")
        through = next(("/".join(bits[:i]) for i in range(1, len(bits)) if _fold("/".join(bits[:i])) in links), None)
        if through:  # a link inside the path (`y -> s/vault.sh` with `s -> scripts`: of_architect on PR 306)
            lt = link_target(links[_fold(through)])
            if lt is None or lt.startswith("/"):
                return None
            dest = posixpath.normpath(posixpath.join(posixpath.dirname(through), lt, dest[len(through) + 1:]))
            if dest in (".", "") or dest.startswith("../"):
                return "" if dest in (".", "") else None
            path, t = dest, None
            if _fold(dest) not in links:
                return dest
            path = links[_fold(dest)]
            continue
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


def _differs(tree, snap, paths, human=()):
    """The PATHS whose state on disk is not SNAP's: content that does not hash to the id SNAP's verified tree names, a
    link that is no link or a file that is one, another executable bit, a file missing where SNAP has one (outside the
    sparse set only when no HUMAN entry covers it), a file still there where SNAP has none, or a link among its
    folders."""
    out = []
    for path in sorted(set(paths)):
        want = snap.get(path)
        if want and want[0] == "160000":
            continue
        full = os.path.join(tree, path)
        if _leading_link(tree, path):
            out.append(path)
            continue
        try:
            st = os.lstat(full)
        except FileNotFoundError:
            if want and (_covered(path, human) or not _sparse_out(tree, path)):
                out.append(path)
            continue
        except OSError:
            out.append(path)
            continue
        if want is None:
            if not stat.S_ISDIR(st.st_mode):
                out.append(path)
            continue
        try:
            if want[0] == "120000":
                if not stat.S_ISLNK(st.st_mode):
                    out.append(path)
                    continue
                data = os.fsencode(os.readlink(full))
            else:
                if not stat.S_ISREG(st.st_mode) or (want[0] == "100755") != bool(st.st_mode & 0o100):
                    out.append(path)
                    continue
                with os.fdopen(os.open(full, os.O_RDONLY | os.O_NOFOLLOW), "rb") as fh:
                    data = fh.read()
        except OSError:
            out.append(path)
            continue
        if _hash(snap.fmt, b"blob", data) != want[1]:
            out.append(path)
    return out


def _leading_link(tree, path):
    """Is one of PATH's folders a link on disk (so a file read there is not the tree's)?"""
    bits = path.split("/")
    for i in range(1, len(bits)):
        try:
            if stat.S_ISLNK(os.lstat(os.path.join(tree, *bits[:i])).st_mode):
                return True
        except OSError:
            return False
    return False


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
    if not _descends(tree, head, to):
        raise Refused(f"{to[:12]} does not fast-forward the tree's {head[:12]}")
    off = _no_filters(tree)  # status runs a clean driver on a file whose stat changed (of_secrets-review on PR 306)
    # never into a submodule's work tree, whose own config names its own drivers (of_architect's third pass on PR 306)
    if _git(tree, "status", "--porcelain", "--untracked-files=no", "--ignore-submodules=dirty", extra=off).stdout.strip():
        raise Refused("the tree has changes of its own: someone wrote it; the owner looks before anything moves")
    old, new = _snapshot(tree, head), _snapshot(tree, to)
    twins = _case_twins(list(new))
    if twins:
        raise Refused("the target holds paths that differ only in case, one file on this volume: " + "; ".join(twins))
    changes = _changes(old, new)
    human = human_set(tree, ff_spec, (old, new))

    def link_target(path):
        data = _read(tree, new, path)
        return None if data is None else data.decode("utf-8", "replace")
    hits = _hits(changes, human, link_target, {_fold(p): p for p, (m, _) in new.items() if m == "120000"})
    if hits:
        raise Refused("it changes what can reach the secret, so this fast-forward stays the owner's: " + ", ".join(hits))
    unchecked = _unchecked(tree, human)
    if unchecked:
        raise Refused("the index leaves these human paths out of the work tree or marks them unchanged (sparse "
                      "checkout, skip-worktree, assume-unchanged), so no check would see them: the owner's: "
                      + ", ".join(unchecked[:20]))
    checked = {p for p, _, _ in changes} | {p for p in set(new) | set(old) if _covered(p, human)}
    blobs = _held(tree, (old, new), checked)  # every blob either checkout writes, verified and held from here on
    pr = github_proof(repo, to, branch, api=api, checks=ff_spec.get("checks"))
    # the checkouts never apply sparse patterns, which could take a human file away (of_architect's fifth pass)
    off = off + ["-c", "core.sparseCheckout=false", "-c", "core.sparseCheckoutCone=false", "-c", "index.sparse=false"]
    store = None
    try:
        store = _private_store((old, new), blobs)  # from here git reads no object from the shared store
        if _git(tree, "rev-parse", "--verify", "HEAD").stdout.strip() != head:  # moved while it was checked
            raise Refused("the tree moved while it was checked; run the command again")
        _git(tree, "checkout", "--quiet", "--detach", to, extra=off, store=store)
        # the whole index is the verified target, and every file it wrote and every human file hashes on disk to the
        # target's ids (of_architect's third and fourth passes on PR 306)
        bad = _index_differs(tree, new) + _differs(tree, new, checked, human)
        if _git(tree, "rev-parse", "--verify", "HEAD", check=False).stdout.strip() != to:
            bad = ["HEAD"] + bad
        if bad:  # not the target's: move back from the same verified objects, and say whether the tree is as it was
            _git(tree, "checkout", "--quiet", "--force", "--detach", head, extra=off, store=store, check=False)
            still = _index_differs(tree, old) + _differs(tree, old, checked, human)
            if _git(tree, "rev-parse", "--verify", "HEAD", check=False).stdout.strip() != head:
                still = ["HEAD"] + still
            raise Refused(f"{', '.join(sorted(set(bad))[:20])} was not the target's verified content after the "
                          f"checkout; moved back to {head[:12]}"
                          + (f", but {', '.join(sorted(set(still))[:20])} still differ from it: the tree is not as it "
                             "was, the owner must look" if still else ", and those files are HEAD's again"))
    finally:
        if store:
            import shutil
            shutil.rmtree(store, ignore_errors=True)
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
