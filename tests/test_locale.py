"""The locale layer (bin/of_locale.py; organic-framework 5462bab): English in the code, the person's language from the
catalogue, and every string the code shows through tr() translated in every catalogue."""
import glob
import os
import re
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_locale  # noqa: E402

BIN = os.path.join(HERE, "..", "bin")
CALL = re.compile(r'''\btr\(\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')((?:\s*"(?:[^"\\]|\\.)*")*)''')


def sources():
    """Every literal key passed to tr() in bin/ (adjacent string literals joined, escapes read)."""
    out = set()
    for path in glob.glob(os.path.join(BIN, "of*")):
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for m in CALL.finditer(text):
            parts = [m.group(1) if m.group(1) is not None else m.group(2)]
            parts += re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(3) or "")
            out.add(bytes("".join(parts), "utf-8").decode("unicode_escape").encode("latin-1").decode("utf-8"))
    return out


class LocaleTest(unittest.TestCase):
    def tearDown(self):
        of_locale.set_language(None)

    def test_lookup_fallback_context_and_placeholders(self):
        self.assertEqual(of_locale.tr("{n} min ago", "en", n=5), "5 min ago")
        self.assertEqual(of_locale.tr("{n} min ago", "ru", n=5), of_locale.catalogue("ru")["{n} min ago"].format(n=5))
        self.assertNotEqual(of_locale.tr("{n} min ago", "ru", n=5), "5 min ago")
        self.assertEqual(of_locale.tr("stuck|pill", "en"), "stuck")       # the context never shows in English
        self.assertEqual(of_locale.tr("stuck|pill", "ru"), of_locale.catalogue("ru")["stuck|pill"])
        self.assertNotEqual(of_locale.catalogue("ru")["stuck|pill"], of_locale.catalogue("ru")["stuck"])
        self.assertEqual(of_locale.tr("no such text", "ru"), "no such text")  # a missing entry: the English source

    def test_the_language_comes_from_owner_yaml(self):
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.CONFIG_DIR
            of_events.CONFIG_DIR = d
            try:
                of_locale.set_language(None)
                self.assertEqual(of_locale.language(), "en")  # no setting: English
                with open(os.path.join(d, "owner.yaml"), "w") as fh:
                    fh.write("# the owner's language\nlanguage: ru\n")
                of_locale.set_language(None)
                self.assertEqual(of_locale.language(), "ru")
                with open(os.path.join(d, "owner.yaml"), "w") as fh:
                    fh.write("language: xx\n")  # no catalogue for it: English
                of_locale.set_language(None)
                self.assertEqual(of_locale.language(), "en")
            finally:
                of_events.CONFIG_DIR = saved

    def test_every_shown_string_is_in_every_catalogue_and_keys_are_english(self):
        keys = sources()
        self.assertGreater(len(keys), 40)
        for lang in of_locale.available():
            cat = of_locale.catalogue(lang)
            missing = sorted(k for k in keys if k not in cat)
            self.assertEqual(missing, [], f"{lang}: no translation for these tr() strings")
            self.assertEqual([k for k in cat if re.search("[\u0400-\u04ff]", k)], [], "keys are the English source")
            for k, v in cat.items():  # a translation keeps the source's placeholders
                self.assertEqual(sorted(re.findall(r"\{(\w+)", k)), sorted(re.findall(r"\{(\w+)", v)), k)


if __name__ == "__main__":
    unittest.main()
