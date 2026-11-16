"""of archive: copy harness transcripts into the raw tier, continuously (docs/storage.md, "Raw tier: every byte").

Each source file is zstd-compressed to ~/.local/state/of/raw/files/<path relative to home>.zst. A file that only grew
(transcripts are appended to; its first 4 KB and the 4 KB before its old end unchanged) gets one more zstd frame with
its new bytes, so the copy is never compressed again whole;
concatenated frames decompress to the whole file. A file that changed otherwise is copied again whole, and the earlier
copy is kept beside it as <name>.<its mtime>.zst: nothing is dropped. Files never copied go first. Runs at low CPU and
IO priority, at most BUDGET seconds a run, one run at a time (raw/archive.lock): the listener runs it every hour (10
minutes when the machine is idle, 1 minute otherwise) and the hook at SessionEnd for that session's file
(of_architect, hand-over b097ba7c: from 2026-11-05 to 11-16 nothing was copied after the first run).
Sources: every Claude config dir (~/.claude*/projects) and ~/.codex/sessions.
"""

import glob
import hashlib
import json
import os
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


def sources():
    roots = glob.glob(os.path.join(HOME, ".claude*", "projects")) + [os.path.join(HOME, ".codex", "sessions")]
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
    """[(source, rel, stat, kind)] to copy: kind `new` (never copied), `grown` (appended to since) or `changed` (else);
    new files first, oldest first within each kind."""
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
        elif len(was) > 3 and st.st_size > was[0] and _head(src, was[0]) == was[2] and _tail(src, was[0]) == was[3]:
            kind = "grown"
        else:
            kind = "changed"
        out.append((src, rel, st, kind))
    order = {"new": 0, "grown": 1, "changed": 2}
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
    import fcntl
    lower_priority()
    root = raw_root()
    lock = open(os.open(os.path.join(os.path.dirname(root), "archive.lock"), os.O_WRONLY | os.O_CREAT, 0o600))
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if not quiet:
            log("another of archive is running")
        return 0
    index_path = os.path.join(os.path.dirname(root), "files.json")
    try:
        with open(index_path) as fh:
            index = json.load(fh)
    except FileNotFoundError:
        index = {}
    t0, done, bytes_in, bytes_out = time.time(), {"new": 0, "grown": 0, "changed": 0}, 0, 0
    todo = plan(index, paths if paths is not None else sources())
    left = 0
    for i, (src, rel, st, kind) in enumerate(todo):
        if budget_s is not None and time.time() - t0 > budget_s:
            left = len(todo) - i
            break
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
        if n:
            of_events.emit("raw_archived", {"files": n, "new": done["new"], "grown": done["grown"],
                                            "changed": done["changed"], "left": left, "bytes_in": bytes_in,
                                            "bytes_out": bytes_out, "seconds": round(time.time() - t0, 1)})
    if not quiet or n:
        verb = "would archive" if dry_run else "archived"
        log(f"{verb} {n} files ({done['new']} new, {done['grown']} grown, {done['changed']} changed; "
            f"{bytes_in / 1e9:.2f} GB" + (f" -> {bytes_out / 1e9:.2f} GB" if not dry_run else "")
            + f"), {left} left for the next run, {time.time() - t0:.0f}s")
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
