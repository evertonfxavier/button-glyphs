"""Songs for a Hero - Definitive Edition: the real table, the real bundled drawings, the profile's shape.

The game's own files are never needed: fake XNB files with the table's geometry stand in for them.
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from button_glyphs import engine, image, xnb  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402
from button_glyphs.profiles.songs_for_a_hero import FILES, PROFILE  # noqa: E402
from button_glyphs.profiles.songs_for_a_hero_art import ART  # noqa: E402
from button_glyphs.profiles.songs_for_a_hero_cells import EXTRA_TEXTURES, TEXTURES  # noqa: E402
from button_glyphs.profiles.xnb_pictures import XnbPictureProfile  # noqa: E402
from test_backend import GLYPH_DIR, make_library  # noqa: E402
from test_xnb import build  # noqa: E402

MASKS = os.path.join(ROOT, "py_modules", "button_glyphs", "masks")
NOT_DRAWN = {
    "Tooltip/Hold", "Tooltip/LEFT_STICK_DOWN", "Tooltip/LEFT_STICK_LEFT", "Tooltip/LEFT_STICK_NEUTRAL",
    "Tooltip/LEFT_STICK_RIGHT", "Tooltip/LEFT_STICK_UP", "Menu/xbox/dup", "Menu/xbox/ddown",
    "Menu/xbox/dleft", "Menu/xbox/dright", "Menu/xbox/lstick", "Menu/xbox/rstick",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fake_original(entry, seed=0):
    """A stand-in for the game's file: the table's geometry (header ends at 85), made-up pixels."""
    block = bytes((i * 7 + seed) & 255 for i in range(entry["width"] * entry["height"] * 4))
    return build(entry["width"], entry["height"], block=block, filler=1)


class TableTest(unittest.TestCase):
    def test_registered_with_the_expected_shape(self):
        self.assertIs(PROFILES["389170"], PROFILE)
        self.assertEqual(PROFILE.name, "Songs for a Hero - Definitive Edition")
        self.assertTrue(PROFILE.files_level)
        self.assertEqual(PROFILE.kind, "loose-files")
        self.assertEqual(PROFILE.revision, 1)
        self.assertEqual(PROFILE.native_style, "xbox")
        self.assertEqual(PROFILE.patch_styles, ("ps",))
        self.assertTrue(PROFILE.original_hint and PROFILE.notes and PROFILE.roles)
        self.assertEqual(len(PROFILE.roles), 4)

    def test_it_draws_24_of_the_36_textures_and_leaves_the_rest(self):
        self.assertEqual(len(TEXTURES), 36)
        self.assertEqual(len(FILES), 24)
        keys = [e["key"] for e in FILES]
        self.assertEqual(len(set(keys)), 24)
        self.assertEqual(set(keys), set(ART))
        self.assertEqual({t["key"] for t in TEXTURES} - set(keys), NOT_DRAWN)
        self.assertEqual(sum(k.startswith("Tooltip/") for k in keys), 14)
        self.assertEqual(sum(k.startswith("Menu/xbox/") for k in keys), 10)
        extra = {t["key"] for t in EXTRA_TEXTURES}
        self.assertFalse(extra & set(keys))                    # the typo duplicate is out of scope

    def test_entries_are_consistent_with_the_measured_table(self):
        by_key = {t["key"]: t for t in TEXTURES}
        names = set()
        for e in FILES:
            t = by_key[e["key"]]
            self.assertEqual(e["file"], "Content/%s.xnb" % e["key"])
            self.assertEqual((e["width"], e["height"]), (t["width"], t["height"]))
            self.assertEqual(e["header"], 85)
            self.assertEqual(e["size"], 85 + e["width"] * e["height"] * 4)
            self.assertEqual(e["sha256"], t["sha256"])
            self.assertEqual(len(e["sha256"]), 64)
            self.assertEqual(e["image"], ART[e["key"]])
            names.add(e["key"].replace("/", "_"))
        self.assertEqual(len(names), 24)                         # one backup file per texture

    def test_the_tiny_files_stay_far_below_the_size_limit(self):
        self.assertLess(max(e["size"] for e in FILES), 4096)
        self.assertLess(sum(e["size"] for e in FILES), 64 * 1024)


class BundledArtTest(unittest.TestCase):
    """The user's own drawings (allowed in the repo): exactly the texture's size, alpha 0/255 only."""

    def test_every_picture_has_the_size_of_its_texture_and_clean_alpha(self):
        for e in FILES:
            rgba, w, h = image.read_png(os.path.join(MASKS, e["image"] + ".png"))
            self.assertEqual((w, h), (e["width"], e["height"]), e["key"])
            self.assertEqual(len(rgba), w * h * 4, e["key"])
            self.assertLessEqual(set(rgba[3::4]), {0, 255}, e["key"])
            self.assertIn(255, set(rgba[3::4]), "%s is fully transparent" % e["key"])
            for i in range(0, len(rgba), 4):
                if rgba[i + 3] == 0:
                    self.assertEqual(tuple(rgba[i:i + 3]), (0, 0, 0), "%s: transparent pixel not (0,0,0,0)" % e["key"])

    def test_preflight_accepts_the_bundled_pictures(self):
        PROFILE.preflight()

    def test_every_picture_file_is_used_once(self):
        files = sorted(n for n in os.listdir(os.path.join(MASKS, "sfh")) if n.endswith(".png"))
        self.assertEqual(files, sorted(os.path.basename(e["image"]) + ".png" for e in FILES))

    def test_render_keeps_length_and_header_and_swaps_the_pixels(self):
        for e in FILES:
            original = fake_original(e)
            new = PROFILE.render(original, e, "ps")
            self.assertEqual(len(new), len(original), e["key"])
            self.assertEqual(new[:85], original[:85], e["key"])
            rgba, _, _ = image.read_png(os.path.join(MASKS, e["image"] + ".png"))
            self.assertEqual(xnb.pixels(new), rgba, e["key"])
            self.assertEqual(new, PROFILE.render(original, e, "ps"))     # deterministic
            self.assertEqual(xnb.parse(new).data_offset, 85)


class RealArtThroughTheEngineTest(unittest.TestCase):
    """The real table geometry and the real pictures, driven by the engine on a fake install."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        common = make_library(self.home, [("389170", "Songs for a Hero - Definitive Edition",
                                           "A Lenda do Herói - O Jogo", "4")])
        self.install = os.path.join(common, "A Lenda do Herói - O Jogo")
        files = []
        for i, e in enumerate(FILES):
            data = fake_original(e, seed=i)
            path = os.path.join(self.install, *e["file"].split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
            files.append(dict(e, sha256=sha(data)))              # the fake build's own hashes
        self.untouched = []
        for t in TEXTURES:                                       # the 12 textures the profile leaves alone
            if t["key"] in ART:
                continue
            path = os.path.join(self.install, "Content", *(t["key"] + ".xnb").split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(fake_original(t, seed=99))
            self.untouched.append(os.path.relpath(path, self.install))
        self.profile = XnbPictureProfile(
            appid="389170", name=PROFILE.name, content_dir="Content", files=files, roles=PROFILE.roles,
            native_style="xbox", revision=1)                     # default loader: the real masks/
        proc = os.path.join(self.home, "proc")
        os.makedirs(proc)
        self.eng = engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={"389170": self.profile}, proc_root=proc)

    def tearDown(self):
        shutil.rmtree(self.home)

    def tree(self):
        out = {}
        for base, _, names in os.walk(self.install):
            for name in names:
                path = os.path.join(base, name)
                with open(path, "rb") as f:
                    out[os.path.relpath(path, self.install)] = sha(f.read())
        return out

    def test_ps_then_original_returns_every_file_byte_for_byte(self):
        pristine = self.tree()
        self.assertEqual(len(pristine), 36)
        self.assertEqual(len(self.untouched), 12)
        for style in ("ps", "original", "ps", "xbox"):
            r = self.eng.set_style("389170", style)
            self.assertTrue(r["ok"], r)
            changed = {k for k, v in self.tree().items() if v != pristine[k]}
            if style == "ps":
                self.assertEqual(len(changed), 24)
                self.assertFalse(changed & set(self.untouched))           # the other 12 stay byte-identical
                st = next(g for g in self.eng.list_games() if g["appid"] == "389170")
                self.assertEqual((st["state"], st["applied"]), ("ok", "ps"))
            else:
                self.assertEqual(self.tree(), pristine, style)


if __name__ == "__main__":
    unittest.main()
