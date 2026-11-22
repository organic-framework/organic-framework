"""yaml.safe_load through libyaml's parser when PyYAML was built with it (a user organization's structure keeper,
message 01a20537-12ad: on a loaded laptop one `of send` cost up to 1.5 cores, nearly all of it the pure-Python parse
of the registries; libyaml parses the largest registry in an eighth of the time, to the same data).

Imported for its effect first by every entry point OF runs through bin/of, the hook included, so every process on one
machine parses with one parser (of_review on organic-framework PR 572: the two disagree on a few texts, such as an
escaped lone surrogate, which libyaml refuses, and a tab after a colon, which the pure parser refuses). The loader is
CSafeLoader: libyaml's parser under the same SafeConstructor and resolver as yaml.safe_load. Without libyaml nothing
changes. `of registry check` parses each registry with both where libyaml is there and reports a text they read
differently (disagreement), so a registry cannot choose its parser by machine."""

import yaml

FAST = getattr(yaml, "CSafeLoader", None) if getattr(yaml, "__with_libyaml__", False) else None

if FAST is not None and not getattr(yaml.safe_load, "_of_fast", False):
    def safe_load(stream):
        return yaml.load(stream, Loader=FAST)

    safe_load._of_fast = True
    safe_load.__doc__ = yaml.safe_load.__doc__
    yaml.safe_load = safe_load


def disagreement(text):
    """None when the pure parser and libyaml's read TEXT the same (or libyaml is not here), else what differs."""
    if FAST is None:
        return None
    out = []
    for loader in (yaml.SafeLoader, FAST):
        try:
            out.append(("ok", yaml.load(text, Loader=loader)))
        except Exception as e:  # noqa: BLE001 - which error does not matter, only whether both refuse
            out.append(("error", type(e).__name__))
    (a, x), (b, y) = out
    if a == b == "error" or (a == b == "ok" and x == y):
        return None
    say = lambda k, v: "refuses it" if k == "error" else "reads it"  # noqa: E731
    return (f"libyaml's parser {say(b, y)} and the pure parser {say(a, x)}" if a != b
            else "the two parsers read different values")
