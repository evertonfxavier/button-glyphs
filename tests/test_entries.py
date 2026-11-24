"""Entry-level patching: several archives per game, original bytes kept inside the files."""
import hashlib
import os
import shutil
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from button_glyphs import engine, entries, image  # noqa: E402
from button_glyphs.forge import ALIGN, Forge  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402
from button_glyphs.profiles.forge_glyphs import ForgeGlyphProfile  # noqa: E402
import forge_fixture as fx  # noqa: E402
from test_backend import GLYPH_DIR, make_library  # noqa: E402

AREA_ENTRY = "Area_DataBlock"
PRIMARY = "DataPC.forge"
AREA = "DataPC_Area.forge"


def slot_of(record):
    """What forge.set_slot needs from an index entry, rebuilt from a trailer record (the old `_Slot`)."""
    return types.SimpleNamespace(slot_off=record["slot_off"], name_off=record["name_off"],
                                 file_id=record["orig"][1])


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def make_profile(area_box=None):
    box = area_box or fx.BOXES["head"]
    primary = fx.test_profile()
    area = {
        "file": AREA, "entry": AREA_ENTRY, "styles": ("ps",),
        "textures": [{
            "id": fx.TEX_ID, "name": "Glyphs_Map", "label": "area glyph", "width": 64, "height": 64,
            "format": "DXT3", "flip_y": True, "boxes": {"head": box},
            "icons": {"ps": {"head": "ps/plain-cross"}},
        }],
    }
    return ForgeGlyphProfile(
        appid="15100", name="Test", file=PRIMARY, datafile="Game Bootstrap Settings",
        textures=primary.textures, roles=primary.roles, styles=primary.styles,
        extra_targets=[area], revision=3,
    )


def head_pixels(path, entry):
    df = Forge(path).load(entry)
    res = df.read_resource(fx.TEX_ID)
    w, h, fmt, off, size = ForgeGlyphProfile._layout(res)
    rgba = image.decode(res[off:off + size], w, h, "DXT3")
    x0, y0, x1, y1 = fx.BOXES["head"]
    return [tuple(rgba[(y * w + x) * 4:(y * w + x) * 4 + 4])
            for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


class EntryEngineTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        common = make_library(self.home, [("15100", "Assassin's Creed", "Assassins Creed", "4")])
        self.dir = os.path.join(common, "Assassins Creed")
        self.primary = os.path.join(self.dir, PRIMARY)
        self.area = os.path.join(self.dir, AREA)
        self.build()
        self.original = {PRIMARY: sha(self.primary), AREA: sha(self.area)}
        self.eng = engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={"15100": make_profile()}, proc_root=os.path.join(self.home, "proc"))
        os.makedirs(os.path.join(self.home, "proc"))

    def tearDown(self):
        shutil.rmtree(self.home)

    def build(self, variant=0):
        fx.build_forge(self.primary, variant=variant)
        fx.build_forge(self.area, variant=variant, entry=AREA_ENTRY)

    def status(self):
        return next(g for g in self.eng.list_games() if g["appid"] == "15100")

    def test_playstation_patches_every_file(self):
        result = self.eng.set_style("15100", "ps")
        self.assertTrue(result["ok"], result)
        for path in (self.primary, self.area):
            self.assertEqual(entries.inspect(path)[0], entries.PATCHED)
            self.assertEqual(engine.read_trailer(path)["style"], "ps")
        # the area glyph is the PlayStation cross, drawn flipped (bottom-up texture)
        px = head_pixels(self.area, AREA_ENTRY)
        self.assertFalse(any(r > 200 and g > 180 and b < 60 and a > 200 for r, g, b, a in px))
        self.assertTrue(any(a > 100 for *_, a in px))
        self.assertEqual(self.status()["state"], "ok")

    def test_xbox_patches_only_files_that_need_it(self):
        self.assertTrue(self.eng.set_style("15100", "xbox")["ok"])
        self.assertEqual(entries.inspect(self.primary)[0], entries.PATCHED)
        self.assertEqual(sha(self.area), self.original[AREA])
        self.assertIsNone(engine.read_trailer(self.area))

    def test_original_restores_exact_bytes(self):
        self.eng.set_style("15100", "ps")
        self.assertTrue(self.eng.set_style("15100", "original")["ok"])
        self.assertEqual(sha(self.primary), self.original[PRIMARY])
        self.assertEqual(sha(self.area), self.original[AREA])
        self.assertEqual(self.status()["state"], "ok")

    def test_switching_styles_never_grows_the_files(self):
        self.eng.set_style("15100", "ps")
        size = os.path.getsize(self.area)
        for style in ("xbox", "ps", "ps", "xbox", "ps"):
            self.eng.set_style("15100", style)
        self.assertEqual(os.path.getsize(self.area), size)
        self.eng.set_style("15100", "original")
        self.assertEqual(sha(self.area), self.original[AREA])

    def test_reapplies_after_game_update(self):
        self.eng.set_style("15100", "ps")
        self.build(variant=1)  # Steam replaced the files with a new version
        new = {PRIMARY: sha(self.primary), AREA: sha(self.area)}
        make_library(self.home, [("15100", "Assassin's Creed", "Assassins Creed", "4")], buildid="200")
        self.assertEqual(self.status()["state"], "pending")

        results = self.eng.sync_all("watch")
        self.assertTrue(results[0]["changed"], results)
        for path in (self.primary, self.area):
            self.assertEqual(entries.inspect(path)[0], entries.PATCHED)
        self.assertIn("build 100 -> 200", self.eng.load_config()["games"]["15100"]["last_event"])
        self.assertFalse(self.eng.sync_all("watch")[0]["changed"])
        self.eng.set_style("15100", "original")
        self.assertEqual(sha(self.primary), new[PRIMARY])
        self.assertEqual(sha(self.area), new[AREA])

    def test_recovers_when_the_index_was_never_repointed(self):
        self.eng.set_style("15100", "ps")
        patched_size = os.path.getsize(self.area)
        state, trailer = entries.inspect(self.area)
        # crash simulation: blob and trailer are on disk but the slot still points at the original
        from button_glyphs.forge import set_slot
        rec = trailer["entries"][0]
        with open(self.area, "r+b") as f:
            set_slot(f, slot_of(rec), rec["orig"][0], rec["orig"][2], rec["orig_name_size"])
        self.assertEqual(entries.inspect(self.area)[0], entries.PARTIAL)

        self.assertTrue(self.eng.sync("15100")["ok"])
        self.assertEqual(entries.inspect(self.area)[0], entries.PATCHED)
        self.assertEqual(os.path.getsize(self.area), patched_size)

    def test_missing_files_are_repaired(self):
        self.eng.set_style("15100", "ps")
        shutil.copyfile(self.area, self.area + ".keep")
        self.build()  # "verify integrity" restored one file only
        shutil.copyfile(self.area + ".keep", self.area)
        fx.build_forge(self.primary)  # primary pristine again, area still patched
        self.assertEqual(self.status()["state"], "pending")
        self.assertTrue(self.eng.sync("15100")["changed"])
        self.assertEqual(self.status()["state"], "ok")

    def test_unsupported_version_leaves_everything_original(self):
        self.eng.profiles["15100"] = make_profile(area_box=(40, 4, 50, 10))  # no glyph there
        result = self.eng.set_style("15100", "ps")
        self.assertFalse(result["ok"])
        self.assertIn("not supported", result["errors"][0])
        self.assertEqual(sha(self.primary), self.original[PRIMARY])
        self.assertEqual(sha(self.area), self.original[AREA])
        self.assertEqual(self.status()["state"], "error")

    def test_moves_a_v01_whole_file_patch_to_the_new_format(self):
        profile = self.eng.profiles["15100"]
        # what v0.1.x left behind: patched primary + whole-file trailer + whole-file backup
        backup = self.eng.backup_path("15100", profile)
        os.makedirs(os.path.dirname(backup))
        shutil.copyfile(self.primary, backup)
        profile.patch(self.primary, "ps", self.eng.icons)
        engine.write_trailer(self.primary, {"style": "ps", "appid": "15100", "revision": 2,
                                            "source_sha256": self.original[PRIMARY]})
        cfg = self.eng.load_config()
        cfg["games"]["15100"] = {"style": "ps", "backup": {"sha256": self.original[PRIMARY]}}
        self.eng.save_config(cfg)
        self.assertEqual(entries.inspect(self.primary)[0], entries.LEGACY)

        self.assertTrue(self.eng.sync("15100")["ok"])
        for path in (self.primary, self.area):
            self.assertEqual(entries.inspect(path)[0], entries.PATCHED)
        self.assertFalse(os.path.exists(backup))
        self.assertNotIn("backup", self.eng.load_config()["games"]["15100"])
        self.assertIn("patch format updated", self.eng.load_config()["games"]["15100"]["last_event"])
        self.eng.set_style("15100", "original")
        self.assertEqual(sha(self.primary), self.original[PRIMARY])

    def test_old_backup_missing_is_reported(self):
        profile = self.eng.profiles["15100"]
        profile.patch(self.primary, "ps", self.eng.icons)
        engine.write_trailer(self.primary, {"style": "ps", "source_sha256": "0" * 64})
        result = self.eng.set_style("15100", "ps")
        self.assertFalse(result["ok"])
        self.assertIn("Verify files", result["errors"][0])


class EntriesUnitTest(unittest.TestCase):
    def test_strange_state_only_removes_our_marker(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "a.forge")
            fx.build_forge(path)
            prof = make_profile()
            target = prof.primary_target()
            info = {"appid": "1", "buildid": "1", "plugin": "t", "revision": 3}
            entries.apply(path, prof, target, "ps", engine.IconLoader(GLYPH_DIR), info)
            state, trailer = entries.inspect(path)
            self.assertEqual(state, entries.PATCHED)
            # the index no longer matches (e.g. Steam rewrote it): nothing else may be cut
            rec = trailer["entries"][0]
            from button_glyphs.forge import set_slot
            with open(path, "r+b") as f:
                set_slot(f, slot_of(rec), 12345, 1, 1)
            with open(path, "rb") as f:
                before = f.read()
            state, trailer = entries.inspect(path)
            self.assertEqual(state, entries.STRANGE)
            entries.revert(path, state, trailer)
            with open(path, "rb") as f:
                after = f.read()
            self.assertEqual(after, before[:-ALIGN])
            self.assertIsNone(engine.read_trailer(path))
        finally:
            shutil.rmtree(d)


class ProfileDataTest(unittest.TestCase):
    def test_every_target_texture_has_its_icons(self):
        for profile in PROFILES.values():
            if not getattr(profile, "entry_level", False):
                continue
            for style in profile.styles:
                for target in profile.targets_for(style):
                    for spec in target["textures"]:
                        icons = profile.icons_for(spec, style)
                        self.assertLessEqual(set(spec["boxes"]), set(icons), (target["file"], spec["label"]))
                        for name in (icons[role] for role in spec["boxes"]):
                            self.assertTrue(os.path.exists(os.path.join(GLYPH_DIR, name + ".png")), name)

    def test_target_files_are_unique_and_primary_comes_first(self):
        profile = PROFILES["15100"]
        files = [t["file"] for t in profile.targets()]
        self.assertEqual(len(files), len(set(files)))
        self.assertEqual(files[0], profile.file)
        self.assertEqual([t["file"] for t in profile.targets_for("xbox")], [profile.file])


if __name__ == "__main__":
    unittest.main()
