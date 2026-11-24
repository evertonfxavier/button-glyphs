"""The UI language catalogs, the status codes the engine emits and the support levels stay in step."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "py_modules"))

import gen_supported_games as gen  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402
from button_glyphs.status_codes import CODES  # noqa: E402

I18N = ROOT / "src" / "i18n"
LANGS = ("en", "pt-BR", "es")
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def catalog(lang):
    return json.loads((I18N / (lang + ".json")).read_text(encoding="utf-8"))


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.cats = {lang: catalog(lang) for lang in LANGS}

    def test_identical_key_sets(self):
        base = set(self.cats["en"])
        for lang in LANGS:
            self.assertEqual(base - set(self.cats[lang]), set(), "%s misses keys" % lang)
            self.assertEqual(set(self.cats[lang]) - base, set(), "%s has extra keys" % lang)

    def test_identical_placeholders_per_key(self):
        for key, text in self.cats["en"].items():
            want = set(PLACEHOLDER.findall(text))
            for lang in LANGS:
                self.assertEqual(set(PLACEHOLDER.findall(self.cats[lang][key])), want, "%s / %s" % (lang, key))

    def test_no_empty_strings(self):
        for lang, cat in self.cats.items():
            for key, text in cat.items():
                self.assertIsInstance(text, str, "%s / %s" % (lang, key))
                self.assertTrue(text.strip(), "%s / %s is empty" % (lang, key))

    def test_other_languages_are_really_translated(self):
        """A copy of the English text left behind is a missed translation (proper names excepted)."""
        same = [k for k, v in self.cats["en"].items()
                if k.startswith(("code.", "apply.", "updates.", "games.", "current.")) and
                v == self.cats["pt-BR"][k] == self.cats["es"][k] and k != "current.lastChange"]
        self.assertEqual(same, [])


class StatusCodeTests(unittest.TestCase):
    def test_every_code_the_engine_emits_is_declared_and_translated(self):
        source = (ROOT / "py_modules" / "button_glyphs" / "engine.py").read_text(encoding="utf-8")
        emitted = set(re.findall(r'\bcode="(\w+)"', source))
        self.assertEqual(emitted, set(CODES), "engine.py codes and status_codes.CODES differ")
        for lang in LANGS:
            cat = catalog(lang)
            for code in CODES:
                self.assertIn("code." + code, cat, "%s lacks code.%s" % (lang, code))

    def test_code_args_are_placeholders_of_its_text(self):
        """Each placeholder in a code's English text is an arg the engine sends for that code."""
        source = (ROOT / "py_modules" / "button_glyphs" / "engine.py").read_text(encoding="utf-8")
        sent = {}
        for m in re.finditer(r'code="(\w+)"(?:, args=\{([^}]*)\})?', source):
            sent.setdefault(m.group(1), set()).update(re.findall(r'"(\w+)":', m.group(2) or ""))
        for code in CODES:
            used = set(PLACEHOLDER.findall(catalog("en")["code." + code]))
            self.assertLessEqual(used, sent.get(code, set()), code)


class SupportLevelTests(unittest.TestCase):
    """profile.support (the UI's dot colour) must follow the game page's 'Support level' line."""

    def test_support_follows_the_game_page(self):
        for appid, profile in PROFILES.items():
            level = gen.read_page(ROOT, appid)["level"]
            want = "full" if level == "confirmed" else "partial"
            self.assertEqual(profile.support, want, "%s: page says %r" % (appid, level))

    def test_support_is_one_of_the_two_levels(self):
        for profile in PROFILES.values():
            self.assertIn(profile.support, ("full", "partial"))


if __name__ == "__main__":
    unittest.main()
