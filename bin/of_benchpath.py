"""Where OF finds Bench, the package manager it runs on (organic-framework/bench, kept by of_bench; docs/bench.md).

One checkout, at the path the machine's owner settings name (`bench:` in ~/.config/of/owner.yaml, set with
`of owner set bench <folder>`), else ~/.local/share/bench. Never an environment variable: whoever set one would choose
the code OF imports. The checkout is code OF runs, outside any sandbox too (the pulse reads an organization's settings
through it), so it is protected as OF's own checkout is (of_spawn.of_checkouts; the guard's list)."""

import os

DEFAULT = os.path.join("~", ".local", "share", "bench")


def _plain(path):
    """owner.yaml's `bench:` as the plain line `of owner set bench` writes, or None; ValueError when the file names
    it in any other form, so OF and the guard, which reads that line itself, never read two values
    (of_secrets-review on PR 575)."""
    import re
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read(1 << 16)
    except OSError:
        return None
    lines = [m.group(1) for m in re.finditer(r"^bench:[ \t]*(.*?)[ \t]*$", text, re.M)]
    mentions = len(re.findall(r"(?:^|[{,\s])['\"]?bench['\"]?\s*:", text))   # the key in any form
    if not lines and not mentions:
        return None
    if len(lines) != 1 or mentions != 1 or not re.fullmatch(r"[^\s'\"&*!|>{}\[\]#,]+", lines[0] or ""):
        raise ValueError(f"{path}: bench: is not the one plain line `of owner set bench` writes")
    return None if lines[0] in ("null", "~", "") else lines[0]


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
