"""of archive: copy harness transcripts into the raw tier before the harness deletes them.

A one-off rescue that is safe to re-run: each source file is zstd-compressed to
~/.local/state/of/raw/files/<path relative to home>.zst, and skipped when the
archived copy already has the same size and mtime. Runs at low CPU and IO priority.
Sources: every Claude config dir (~/.claude*/projects) and ~/.codex/sessions.
"""

import glob
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


def archive(dry_run=False, log=print):
    if zstd is None:
        raise SystemExit("of archive needs Python 3.14+ (compression.zstd)")
    lower_priority()
    root = raw_root()
    index_path = os.path.join(of_events.STATE_DIR, "raw", "files.json")
    try:
        with open(index_path) as fh:
            index = json.load(fh)
    except FileNotFoundError:
        index = {}
    t0, n_new, n_skip, bytes_in, bytes_out = time.time(), 0, 0, 0, 0
    for src in sources():
        try:
            st = os.stat(src)
        except OSError:
            continue
        rel = os.path.relpath(src, HOME)
        key = [st.st_size, int(st.st_mtime)]
        if index.get(rel) == key:
            n_skip += 1
            continue
        dst = os.path.join(root, rel + ".zst")
        if dry_run:
            n_new += 1
            bytes_in += st.st_size
            continue
        os.makedirs(os.path.dirname(dst), mode=0o700, exist_ok=True)
        tmp = dst + ".tmp"
        try:
            with open(src, "rb") as fh:
                data = fh.read()
            out = zstd.compress(data, level=9)
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
                fh.write(out)
            os.replace(tmp, dst)
        except OSError as e:
            log(f"skip {rel}: {e}")
            continue
        index[rel] = key
        n_new += 1
        bytes_in += len(data)
        bytes_out += len(out)
        if n_new % 200 == 0:
            _save(index_path, index)
            log(f"  {n_new} files, {bytes_in / 1e9:.2f} GB -> {bytes_out / 1e9:.2f} GB")
    if not dry_run:
        _save(index_path, index)
        of_events.emit("raw_archived", {"files": n_new, "skipped": n_skip, "bytes_in": bytes_in, "bytes_out": bytes_out,
                                         "seconds": round(time.time() - t0, 1)})
    verb = "would archive" if dry_run else "archived"
    log(f"{verb} {n_new} files ({bytes_in / 1e9:.2f} GB"
        + (f" -> {bytes_out / 1e9:.2f} GB" if not dry_run else "") + f"), {n_skip} unchanged, {time.time() - t0:.0f}s")
    return 0


def _save(path, index):
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(index, fh)
    os.replace(tmp, path)


if __name__ == "__main__":
    sys.exit(archive("--dry-run" in sys.argv))
