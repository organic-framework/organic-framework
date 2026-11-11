"""The locale layer: a person reads OF in their own language (organic-framework 5462bab; the founder, 2026-11-11).

Every string in the code is English and is the key. A translation lives only in the catalogue,
`bin/locale/<language>.json` ({English source: translation}). The person's language is `language` in
`~/.config/of/owner.yaml` on their machine (English by default; `of init --language` writes it).

API (for every module that shows text to a person: screens, panels, popups, cards, text typed into panes):
- `tr(source, **kw)`: the source string in the person's language, `.format(**kw)` applied after the lookup, so a
  catalogue entry keeps the placeholders: `tr("{n} min ago", n=5)` gives the catalogue's entry for "{n} min ago" with 5 in it. A missing entry falls back to
  the English source. A context after "|" tells two uses of one English word apart: `tr("stuck|pill")`.
- `language()`: "en" or a catalogue's language; `set_language(lang)` pins it (tests, a one-off render).
- `catalogue(lang)`: the loaded entries.

JSON, not YAML: the hook imports what it shows under interpreters without PyYAML. Text in another language is
allowed in the tree only in the catalogue, in classifier patterns that match what people type, and in their tests;
the pre-publish check enforces it.
"""

import json
import os
import re

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")
DEFAULT = "en"
_lang = None
_cats = {}
_LANG_LINE = re.compile(r"^language:\s*['\"]?([A-Za-z]{2,3})['\"]?\s*(?:#.*)?$", re.M)


def available():
    """The languages with a catalogue, besides English."""
    try:
        return sorted(f[:-5] for f in os.listdir(HERE) if f.endswith(".json"))
    except OSError:
        return []


def _read_setting():
    import of_events
    try:
        with open(of_events.config_path("owner.yaml")) as fh:
            m = _LANG_LINE.search(fh.read())
    except OSError:
        return DEFAULT
    lang = (m.group(1).lower() if m else DEFAULT)
    return lang if lang == DEFAULT or lang in available() else DEFAULT


def owner_setting(key, default=None):
    """A plain `key: value` line of ~/.config/of/owner.yaml (language, surface), read without PyYAML."""
    import of_events
    try:
        with open(of_events.config_path("owner.yaml")) as fh:
            m = re.search(rf"^{re.escape(key)}:\s*['\"]?([^'\"#\n]*?)['\"]?\s*(?:#.*)?$", fh.read(), re.M)
    except OSError:
        return default
    return (m.group(1).strip() or default) if m else default


def language():
    global _lang
    if _lang is None:
        _lang = _read_setting()
    return _lang


def set_language(lang=None):
    """Pin the language (None: read the setting again on the next call)."""
    global _lang
    _lang = lang


def catalogue(lang):
    if lang == DEFAULT:
        return {}
    if lang not in _cats:
        try:
            with open(os.path.join(HERE, f"{lang}.json"), encoding="utf-8") as fh:
                doc = json.load(fh)
            _cats[lang] = {k: v for k, v in doc.items() if not k.startswith("_")}
        except (OSError, ValueError):
            _cats[lang] = {}
    return _cats[lang]


def variants(source):
    """The source string and its translation in every catalogue, for matching what a person typed or chose in any
    of OF's languages (a popup answer "yes" in theirs)."""
    out = [source.split("|", 1)[0]]
    for lang in available():
        v = catalogue(lang).get(source)
        if v and v not in out:
            out.append(v)
    return out


def tr(source, lang=None, **kw):
    """`source` may carry a context after "|" ("stuck|pill") when one English word needs two translations; the
    English text drops it."""
    text = catalogue(lang or language()).get(source)
    if text is None:
        text = source.split("|", 1)[0]
    return text.format(**kw) if kw else text
