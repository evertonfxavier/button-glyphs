"""The supported-games list must stay in step with the profile registry and the game pages."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "py_modules"))

import gen_supported_games as gen  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402


class SupportedGamesTests(unittest.TestCase):
    def test_generated_files_are_up_to_date(self):
        stale = [str(p.relative_to(ROOT)) for p, content in gen.targets(ROOT)
                 if not p.exists() or p.read_text() != content]
        self.assertEqual(stale, [], "run: python3 tools/gen_supported_games.py")

    def test_every_registered_game_has_a_valid_page(self):
        for appid in PROFILES:
            page = gen.read_page(ROOT, appid)
            self.assertIn(page["level"], gen.LEVELS)
            self.assertTrue(page["summary"])

    def test_every_game_is_listed(self):
        text = (ROOT / "SUPPORTED_GAMES.md").read_text()
        for appid, profile in PROFILES.items():
            self.assertIn(profile.name, text)
            self.assertIn(str(appid), text)

    def test_page_without_summary_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            games = Path(tmp) / "docs" / "games"
            games.mkdir(parents=True)
            (games / "1-x.md").write_text("# X\n\n| Status | ok |\n")
            with self.assertRaises(gen.PageError):
                gen.read_page(Path(tmp), 1)

    def test_unknown_support_level_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            games = Path(tmp) / "docs" / "games"
            games.mkdir(parents=True)
            (games / "1-x.md").write_text("| Summary | s |\n| Support level | great |\n")
            with self.assertRaises(gen.PageError):
                gen.read_page(Path(tmp), 1)


if __name__ == "__main__":
    unittest.main()
