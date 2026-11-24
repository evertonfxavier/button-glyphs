"""Games that already have an Xbox/PS option in their own settings: the list, its loader and the engine's listing."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))
sys.path.insert(0, str(ROOT / "tests"))

from button_glyphs import engine, ingame  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402
from test_backend import GLYPH_DIR, make_library  # noqa: E402

GOOD = {"appid": "42", "name": "Some Game", "reported_by": "someone", "reported_on": "2026-11-24"}


class FileTests(unittest.TestCase):
    def test_the_shipped_file_is_valid_json_and_passes_the_schema(self):
        raw = json.loads(Path(ingame.PATH).read_text(encoding="utf-8"))
        self.assertIsInstance(raw, list)
        self.assertEqual(len(ingame.load()), len(raw))

    def test_appids_are_unique(self):
        ids = [e["appid"] for e in ingame.load()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_game_with_a_profile_is_also_in_game(self):
        self.assertEqual(set(ingame.by_appid()) & set(PROFILES), set())

    def test_digimon_is_recorded_as_a_report_not_a_verified_fact(self):
        entry = ingame.by_appid()["1984270"]
        self.assertEqual(entry["name"], "Digimon Story Time Stranger")
        self.assertEqual((entry["reported_by"], entry["reported_on"]), ("the project owner", "2026-11-24"))

    def test_the_file_ships_inside_py_modules(self):
        self.assertEqual(Path(ingame.PATH).parent, ROOT / "py_modules" / "button_glyphs")


class LoaderTests(unittest.TestCase):
    def bad(self, **change):
        entry = {**GOOD, **change}
        entry = {k: v for k, v in entry.items() if v is not None}
        with self.assertRaises(ingame.InGameError, msg=str(change)):
            ingame.validate([entry])

    def test_a_good_entry_gets_its_optional_fields_defaulted(self):
        out = ingame.validate([GOOD])
        self.assertEqual((out[0]["where"], out[0]["note"]), ("", ""))

    def test_bad_entries_are_rejected(self):
        self.bad(appid="12a")
        self.bad(appid="")
        self.bad(appid=42)
        self.bad(name=" ")
        self.bad(name=None)
        self.bad(reported_by=None)
        self.bad(reported_on="24/11/2026")
        self.bad(reported_on="2026-13-40")
        self.bad(where=5)
        self.bad(extra="x")

    def test_duplicates_and_wrong_shapes_are_rejected(self):
        with self.assertRaises(ingame.InGameError):
            ingame.validate([GOOD, dict(GOOD)])
        with self.assertRaises(ingame.InGameError):
            ingame.validate({"appid": "1"})
        with self.assertRaises(ingame.InGameError):
            ingame.validate(["x"])

    def test_invalid_json_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.json")
            Path(path).write_text("[{", encoding="utf-8")
            with self.assertRaises(ingame.InGameError):
                ingame.load(path)


class EngineListingTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        make_library(self.home, [("1984270", "Digimon Story Time Stranger", "Digimon", "4"),
                                 ("555", "Other Game", "Other", "4")])

    def tearDown(self):
        shutil.rmtree(self.home)

    def engine(self, **kw):
        return engine.Engine(home=self.home, settings_dir=os.path.join(self.home, "s"),
                             runtime_dir=os.path.join(self.home, "r"), glyph_dir=GLYPH_DIR,
                             proc_root=os.path.join(self.home, "proc"), **kw)

    def games(self, **kw):
        return {g["appid"]: g for g in self.engine(**kw).list_games()}

    def test_an_in_game_game_is_listed_unsupported_with_its_level(self):
        g = self.games()["1984270"]
        self.assertEqual(set(g), {"appid", "name", "supported", "buildid", "support"})
        self.assertFalse(g["supported"])
        self.assertEqual(g["support"], "ingame")

    def test_a_game_that_is_neither_stays_exactly_as_before(self):
        self.assertEqual(set(self.games()["555"]), {"appid", "name", "supported", "buildid"})

    def test_where_and_note_become_one_how_line(self):
        entries = ingame.by_appid()
        entries["1984270"] = {**entries["1984270"], "where": "Options > Controls.", "note": "Restart needed."}
        g = self.games(ingame_games=entries)["1984270"]
        self.assertEqual(g["how"], "Options > Controls. Restart needed.")

    def test_a_profile_wins_and_the_game_is_never_marked_in_game(self):
        make_library(self.home, [("405290", "Charlie Murder", "CM", "4")])
        entries = {"405290": {**GOOD, "appid": "405290", "where": "", "note": ""}}
        g = self.games(ingame_games=entries)["405290"]
        self.assertTrue(g["supported"])
        self.assertEqual(g["support"], "full")
        self.assertNotIn("how", g)

    def test_an_unreadable_list_never_breaks_the_listing(self):
        real = ingame.by_appid

        def broken(path=None):
            raise ingame.InGameError("broken on purpose")

        ingame.by_appid = broken
        try:
            games = self.games()
        finally:
            ingame.by_appid = real
        self.assertNotIn("support", games["1984270"])
        self.assertEqual(set(games), {"1984270", "555"})


if __name__ == "__main__":
    unittest.main()
