"""Where OF finds Bench, the package manager it runs on (organic-framework/bench, kept by of_bench; docs/bench.md).

One checkout, at the path the machine's owner settings name (`bench:` in ~/.config/of/owner.yaml, set with
`of owner set bench <folder>`), else ~/.local/share/bench. Never an environment variable: whoever set one would choose
the code OF imports. The checkout is code OF runs wherever `of bench` runs, outside any session's sandbox too, so it is
protected as OF's own checkout is (of_spawn.of_checkouts; the guard's list)."""

import os

DEFAULT = os.path.join("~", ".local", "share", "bench")


_KEY = None


def _plain(path):
    """owner.yaml's `bench:` as the guard reads it (of_guard._bench_home: the last top-level `bench:` line, a plain or
    quoted scalar, an unquoted `#` comment dropped), or None; ValueError when YAML reads the file to another value,
    so OF and the guard never read two (of_secrets-review on PR 575)."""
    import re
    import stat
    try:   # never waiting on a pipe, and only a regular file (of_review on PR 575)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        text = os.read(fd, 1 << 16).decode("utf-8", "replace")
    except OSError:
        return None
    finally:
        os.close(fd)
    key = re.compile(r"""^(?:bench|'bench'|"bench")[ \t]*:[ \t]*(.*?)[ \t]*$""")   # of_guard._BENCH_KEY
    v = None
    for line in text.splitlines():
        m = key.match(line)
        if m:
            raw = m.group(1).strip()
            if raw[:1] in ("'", '"'):
                end = raw.find(raw[0], 1)
                v = raw[1:end] if end > 0 else None
            else:
                v = re.split(r"\s#", raw, 1)[0].strip() or None
    v = None if v in ("null", "~") else v
    try:
        import yaml
        doc = yaml.safe_load(text)
        y = doc.get("bench") if isinstance(doc, dict) else None
    except Exception:  # noqa: BLE001 - a file YAML cannot read: the line alone, as the guard
        return v
    if (str(y) if y is not None else None) != v:
        raise ValueError(f"{path}: bench: reads one way by line and another by YAML; write it as `of owner set "
                         f"bench` does")
    return v


def home():
    """The Bench checkout's folder, resolved (it may not exist). ImportError when owner.yaml names it in a form
    other than the plain line the setter writes."""
    try:
        import of_init
        v = _plain(of_init._config_path("owner.yaml"))
    except ValueError as e:
        raise ImportError(str(e))
    except Exception:  # noqa: BLE001 - no owner settings: the default
        v = None
    return os.path.realpath(os.path.expanduser(str(v or DEFAULT)))


def ensure():
    """Put Bench's bin/ at the end of sys.path, after OF's own (so no module of OF's is shadowed), and return it;
    ImportError, saying where it looked and how to set it, when no bench.py is there."""
    import importlib.util
    import sys
    b = os.path.join(home(), "bin")
    f = os.path.join(b, "bench.py")
    if not os.path.isfile(f):
        raise ImportError(f"Bench is not at {home()}: clone organic-framework/bench there, or name its folder with "
                          f"`of owner set bench <folder>` (docs/bench.md)")
    # the module comes from this file by its path, never from whichever bench.py comes first on the path
    # (of_secrets-review on PR 575)
    have = sys.modules.get("bench")
    if have is None or os.path.realpath(getattr(have, "__file__", "") or "") != os.path.realpath(f):
        spec = importlib.util.spec_from_file_location("bench", f)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["bench"] = mod
        spec.loader.exec_module(mod)
    if b not in sys.path:
        sys.path.append(b)
    return b
