"""yaml.safe_load through libyaml's parser when PyYAML was built with it (a user organization's structure keeper,
message 01a20537-12ad: on a loaded laptop one `of send` cost up to 1.5 cores, nearly all of it the pure-Python parse
of the registries; libyaml parses the largest registry in an eighth of the time, to the same data).

Imported for its effect by the long-lived and the frequent entry points (bin/of, the hook, the listener). The loader
is CSafeLoader: libyaml's parser under the same SafeConstructor and resolver as yaml.safe_load. Without libyaml
nothing changes."""

import yaml

FAST = getattr(yaml, "CSafeLoader", None) if getattr(yaml, "__with_libyaml__", False) else None

if FAST is not None and not getattr(yaml.safe_load, "_of_fast", False):
    def safe_load(stream):
        return yaml.load(stream, Loader=FAST)

    safe_load._of_fast = True
    safe_load.__doc__ = yaml.safe_load.__doc__
    yaml.safe_load = safe_load
