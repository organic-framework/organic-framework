"""Where OF finds Bench, the package manager it runs on (organic-framework/bench, kept by of_bench; docs/bench.md).

One checkout, at the path the machine's owner settings name (`bench:` in ~/.config/of/owner.yaml, set with
`of owner set bench <folder>`), else ~/.local/share/bench. Never an environment variable: whoever set one would choose
the code OF imports. The checkout is code OF runs, outside any sandbox too (the pulse reads an organization's settings
through it), so it is protected as OF's own checkout is (of_spawn.of_checkouts; the guard's list)."""

import os

DEFAULT = os.path.join("~", ".local", "share", "bench")


def home():
    """The Bench checkout's folder, resolved (it may not exist)."""
    try:
        import of_init
        v = of_init.owner_settings().get("bench")
    except Exception:  # noqa: BLE001 - no owner settings: the default
        v = None
    return os.path.realpath(os.path.expanduser(str(v or DEFAULT)))


def ensure():
    """Put Bench's bin/ at the end of sys.path, after OF's own (so no module of OF's is shadowed), and return it;
    ImportError, saying where it looked and how to set it, when no bench.py is there."""
    import sys
    b = os.path.join(home(), "bin")
    if not os.path.isfile(os.path.join(b, "bench.py")):
        raise ImportError(f"Bench is not at {home()}: clone organic-framework/bench there, or name its folder with "
                          f"`of owner set bench <folder>` (docs/bench.md)")
    if b not in sys.path:
        sys.path.append(b)
    return b
