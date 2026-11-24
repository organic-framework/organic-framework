"""of archive: copy harness transcripts into the raw tier, continuously (docs/storage.md, "Raw tier: every byte").

Each source file is zstd-compressed to ~/.local/state/of/raw/files/<path relative to home>.zst. A file that only grew
(transcripts are appended to; its first 4 KB and the 4 KB before its old end unchanged) gets one more zstd frame with
its new bytes, so the copy is never compressed again whole; concatenated frames decompress to the whole file. A
transcript (.jsonl) whose time alone moved (its size, first 4 KB and last 4 KB as at the last copy) is not copied
again; any other file of the same size with a new time is (a folder memory can take a same-length edit in its middle):
live sessions' transcripts get a new mtime every hour with no new byte (seen on 2026-11-21, by of_capacity; the writer
not yet identified), and each such hour had kept one more whole copy (4,273 copies, 23.5 GiB, each the size of the copy
after it). A file that changed otherwise is copied again whole, and the earlier copy is kept beside it as <name>.<its
mtime>.zst: nothing is dropped. Files never copied go first. Runs at low CPU and
IO priority, at most BUDGET seconds a run, one run at a time (raw/archive.lock): the listener runs it every hour (10
minutes when the machine is idle, 1 minute otherwise) and the hook at SessionEnd for that session's file
(of_architect, hand-over b097ba7c: from 2026-11-05 to 11-16 nothing was copied after the first run).
Sources: every Claude config dir (~/.claude*/projects) and each Codex seat's sessions folder (~/.codex/sessions and
every CODEX_HOME of_accounts lists inside the home).
"""

import glob
import hashlib
import json
import os
import re
import sys
import time

import of_events

try:
    import compression.zstd as zstd
except ImportError:  # Python < 3.14
    zstd = None

HOME = os.path.expanduser("~")
HEAD = 4096  # the bytes whose hash tells an appended file from a rewritten one
LEVEL = 3    # zstd level: fast enough for a busy laptop; the store's blobs use their own


def raw_root():
    root = os.path.join(of_events.STATE_DIR, "raw", "files")
    os.makedirs(root, mode=0o700, exist_ok=True)
    return root


def codex_roots(seats=None):
    """Each Codex seat's sessions folder (its CODEX_HOME, of_accounts.codex_seats), ~/.codex's among them: a Codex
    position runs in a seat of its own, whose rollouts ~/.codex/sessions alone never held (W6 of hand-over 9ee1db9f,
    of_platform's check, 2026-11-24). Only a seat inside the home folder, since a copy is kept at its path there."""
    out = [os.path.join(HOME, ".codex", "sessions")]
    try:
        if seats is None:
            import of_accounts
            seats = of_accounts.codex_seats()
        real_home = os.path.realpath(HOME)
        for s in seats or []:
            home = os.path.realpath(os.path.expanduser(str(s.get("home") or "")))
            if home.startswith(real_home + os.sep):
                # under HOME as it is written, never its resolved form: a copy is kept at the path relative to HOME,
                # and a resolved one against an unresolved HOME lay outside raw/files (of_review on PR 638)
                out.append(os.path.join(HOME, os.path.relpath(home, real_home), "sessions"))
    except Exception:  # noqa: BLE001 - no seat list: ~/.codex alone, as before
        pass
    seen, roots = set(), []
    for r in out:
        if os.path.realpath(r) not in seen:
            seen.add(os.path.realpath(r))
            roots.append(r)
    return roots


ROLLOUT = re.compile(r"(?:^|/)rollout-[^/]*\.jsonl(?:\.zst)?$")


def harness_of(path):
    """codex for a Codex rollout (rollout-<time>-<thread id>.jsonl, in whichever seat it lies), else claude-code."""
    return "codex" if ROLLOUT.search(str(path)) or "/.codex/" in str(path) else "claude-code"


def sources():
    roots = glob.glob(os.path.join(HOME, ".claude*", "projects")) + codex_roots()
    for root in roots:
        for dirpath, _, files in os.walk(root):
            for f in files:
                yield os.path.join(dirpath, f)


def lower_priority():
    try:
        os.nice(19)
    except OSError:
        pass
    try:  # macOS: background QoS also throttles disk IO
        import ctypes
        libc = ctypes.CDLL("libc.dylib")
        PRIO_DARWIN_THREAD, PRIO_DARWIN_BG = 3, 0x1000
        libc.setpriority(PRIO_DARWIN_THREAD, 0, PRIO_DARWIN_BG)
    except Exception:
        pass


def _head(path, n):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read(min(n, HEAD))).hexdigest()


def _tail(path, n):
    """The hash of the HEAD bytes just before offset N: with _head, what tells an append from a change (of_review on PR
    246: a change past the first 4 KB, then an append, was taken for an append by the head alone)."""
    with open(path, "rb") as fh:
        fh.seek(max(0, n - HEAD))
        return hashlib.sha256(fh.read(min(n, HEAD))).hexdigest()


def plan(index, paths):
    """[(source, rel, stat, kind)] to copy: kind `new` (never copied), `touched` (a .jsonl transcript whose time alone
    moved: nothing to copy, the index takes its new time), `grown` (appended to since) or `changed` (else); new files
    first, oldest first within each kind."""
    out = []
    for src in paths:
        try:
            st = os.stat(src)
        except OSError:
            continue
        rel = os.path.relpath(src, HOME)
        was = index.get(rel)
        if was and was[:2] == [st.st_size, int(st.st_mtime)]:
            continue
        if not was:
            kind = "new"
        elif len(was) > 3 and (st.st_size > was[0] or (src.endswith(".jsonl") and st.st_size == was[0])) and \
                _head(src, was[0]) == was[2] and _tail(src, was[0]) == was[3]:
            # the same size counts as touched only for a transcript, which is appended to and never edited in place: a
            # folder memory can take a same-length edit between its two 4 KB spans (of_review on PR 499)
            kind = "grown" if st.st_size > was[0] else "touched"
        else:
            kind = "changed"
        out.append((src, rel, st, kind))
    order = {"new": 0, "touched": 1, "grown": 2, "changed": 3}
    return sorted(out, key=lambda x: (order[x[3]], x[2].st_mtime))


def _write(path, data, append=False):
    if append:
        with open(os.open(path, os.O_WRONLY | os.O_APPEND), "ab") as fh:
            fh.write(data)
        return
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def archive(dry_run=False, log=print, paths=None, budget_s=None, quiet=False):
    """Copy what changed since the last run, within BUDGET_S seconds. PATHS: only these files (the hook's
    SessionEnd); default every source. Returns 0."""
    if zstd is None:
        raise SystemExit("of archive needs Python 3.14+ (compression.zstd)")
    lower_priority()
    root = raw_root()
    lock = _lock(root, log, quiet)
    if lock is None:
        return 0
    index_path = os.path.join(os.path.dirname(root), "files.json")
    try:
        with open(index_path) as fh:
            index = json.load(fh)
    except FileNotFoundError:
        index = {}
    t0, done, bytes_in, bytes_out = time.time(), {"new": 0, "grown": 0, "changed": 0}, 0, 0
    touched = 0
    todo = plan(index, paths if paths is not None else sources())
    left = 0
    for i, (src, rel, st, kind) in enumerate(todo):
        if budget_s is not None and time.time() - t0 > budget_s:
            left = len(todo) - i
            break
        if kind == "touched":   # the same bytes under a new time: the copy holds them all already
            if not dry_run:
                index[rel] = [st.st_size, int(st.st_mtime)] + index[rel][2:4]
            touched += 1
            continue
        if dry_run:
            done[kind] += 1
            bytes_in += st.st_size - (index[rel][0] if kind == "grown" else 0)
            continue
        dst = os.path.join(root, rel + ".zst")
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        try:
            with open(src, "rb") as fh:
                if kind == "grown":
                    fh.seek(index[rel][0])
                data = fh.read(st.st_size - (index[rel][0] if kind == "grown" else 0))
            out = zstd.compress(data, level=LEVEL)
            if kind == "grown" and os.path.exists(dst):
                _write(dst, out, append=True)   # one more frame: the copy decompresses to the whole file
            else:
                if kind == "changed" and os.path.exists(dst):   # keep the earlier copy: nothing is dropped
                    os.replace(dst, dst[:-4] + f".{index[rel][1]}.zst")
                if kind == "grown":   # the copy is gone: copy whole
                    with open(src, "rb") as fh:
                        data = fh.read(st.st_size)
                    out = zstd.compress(data, level=LEVEL)
                _write(dst, out)
        except OSError as e:
            log(f"skip {rel}: {e}")
            continue
        index[rel] = [st.st_size, int(st.st_mtime), _head(src, st.st_size), _tail(src, st.st_size)]
        done[kind] += 1
        bytes_in += len(data)
        bytes_out += len(out)
        if sum(done.values()) % 200 == 0:
            _save(index_path, index)
    n = sum(done.values())
    if not dry_run:
        _save(index_path, index)
        if n or touched:   # a run that only took new times records them too (of_platform on PR 499)
            of_events.emit("raw_archived", {"files": n, "new": done["new"], "grown": done["grown"],
                                            "changed": done["changed"], "touched": touched, "left": left,
                                            "bytes_in": bytes_in,
                                            "bytes_out": bytes_out, "seconds": round(time.time() - t0, 1)})
    if not quiet or n:
        verb = "would archive" if dry_run else "archived"
        log(f"{verb} {n} files ({done['new']} new, {done['grown']} grown, {done['changed']} changed; "
            f"{bytes_in / 1e9:.2f} GB" + (f" -> {bytes_out / 1e9:.2f} GB" if not dry_run else "")
            + f"), {left} left for the next run, {time.time() - t0:.0f}s"
            + (f"; {touched} with only a new time, not copied" if touched else ""))
    return 0


KEPT = re.compile(r"^(?P<base>.+)\.(?P<mtime>\d+)\.zst$")


def _lock(root, log, quiet):
    """The archive's one lock (raw/archive.lock), or None when another run holds it."""
    import fcntl
    lock = open(os.open(os.path.join(os.path.dirname(root), "archive.lock"), os.O_WRONLY | os.O_CREAT, 0o600))
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        if not quiet:
            log("another of archive is running")
        return None
    return lock


def _file_hash(path):
    """sha256 of the copy PATH's own bytes, read in pieces."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for piece in iter(lambda: fh.read(1 << 20), b""):
            h.update(piece)
    return h.hexdigest()


def kept_chains(root, index):
    """{current copy: [its kept copies, oldest first]}: a kept copy is <name>.<mtime>.zst beside <name>.zst, where
    <name> is a source the index knows and <name>.<mtime> is not: a source may itself be named so, beside another one,
    and its copy is no kept copy (of_review on PR 502)."""
    out = {}
    for dirpath, _, files in os.walk(root):
        names = set(files)
        here = os.path.relpath(dirpath, root)
        for f in files:
            m = KEPT.match(f)
            if not m or m.group("base") + ".zst" not in names:
                continue
            rel = os.path.normpath(os.path.join(here, f[:-4]))
            base = os.path.normpath(os.path.join(here, m.group("base")))
            if rel in index or base not in index:
                continue
            out.setdefault(os.path.join(dirpath, m.group("base") + ".zst"), []).append(
                (int(m.group("mtime")), os.path.join(dirpath, f)))
    return {cur: [p for _, p in sorted(kept)] for cur, kept in out.items()}


def drop_identical(dry_run=False, log=print, budget_s=None, quiet=False):
    """Delete each kept copy whose bytes are the same as the copy after it (the next kept copy, else the current one):
    the same size and sha256, so that copy holds every byte of it and no byte of the raw tier is dropped. 2026-11-21
    (of_capacity, message 01a1fc2a-8d57): a file whose time alone moved was copied whole every hour, and 4,273 kept copies
    were the size of the copy after them. Every copy is one zstd compression of the whole file at LEVEL, so the same
    content gives the same bytes; a copy the same in content alone (a current copy grown by frames never is) stays.
    Only copies of the same size are read. One run at a time with the archive (its lock); within BUDGET_S seconds, the
    next run goes on. Returns 0."""
    lower_priority()
    root = raw_root()
    lock = _lock(root, log, quiet)
    if lock is None:
        return 0
    t0, dropped, freed, kept_n, left = time.time(), 0, 0, 0, 0
    try:
        try:   # the archive's index tells a kept copy from a source's own copy; with none, nothing is dropped
            with open(os.path.join(os.path.dirname(root), "files.json")) as fh:
                index = json.load(fh)
        except (OSError, ValueError) as e:
            log(f"of archive --drop-identical: the index does not read ({type(e).__name__}); nothing dropped")
            return 0
        for cur, kept in sorted(kept_chains(root, index).items()):
            hashes, after = {}, cur

            def same(a, b):
                try:
                    if os.path.getsize(a) != os.path.getsize(b):
                        return False
                    for p in (a, b):
                        if p not in hashes:
                            hashes[p] = _file_hash(p)
                except OSError as e:
                    log(f"skip {os.path.relpath(a, root)}: {e}")
                    return False
                return hashes[a] == hashes[b]

            for i in range(len(kept) - 1, -1, -1):   # newest first: each is compared with the copy after it
                if budget_s is not None and time.time() - t0 > budget_s:
                    left += i + 1
                    break
                path = kept[i]
                if same(path, after):   # AFTER, the copy after it that stays, holds the same bytes
                    size = os.path.getsize(path)
                    if not dry_run:
                        os.unlink(path)
                    dropped, freed = dropped + 1, freed + size
                else:
                    kept_n, after = kept_n + 1, path
    finally:
        lock.close()
    if not quiet or dropped:
        verb = "would drop" if dry_run else "dropped"
        log(f"{verb} {dropped} kept copies the same as the copy after them ({freed / 2 ** 30:.2f} GiB); {kept_n} "
            f"differ and stay; {left} left for the next run, {time.time() - t0:.0f}s")
    return 0


def stats(root=None):
    """{files, bytes, newest}: the raw tier's copies, their compressed size, and the newest copy's time (ISO)."""
    root = root or os.path.join(of_events.STATE_DIR, "raw", "files")
    n, size, newest = 0, 0, 0.0
    for dirpath, _, files in os.walk(root):
        for f in files:
            if not f.endswith(".zst"):
                continue
            try:
                st = os.stat(os.path.join(dirpath, f))
            except OSError:
                continue
            n, size, newest = n + 1, size + st.st_size, max(newest, st.st_mtime)
    return {"files": n, "bytes": size,
            "newest": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(newest)) if newest else None}


def _save(path, index):
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(index, fh)
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(archive("--dry-run" in sys.argv))
