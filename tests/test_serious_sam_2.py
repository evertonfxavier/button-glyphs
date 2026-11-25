"""The Serious Sam 2 profile: its table, its bundled art (the real PNGs), the shapes it draws, and the real profile through the engine.

The real game's originals are never in the repo, so the real profile is exercised with SYNTHETIC originals of the real
size and layout (a 128x128 raw-BGRA .tex per button, the game's transparent-pixel convention) and the real bundled
pictures. Pinned hashes are checked against the generated table, not against game data.
"""
import os
import shutil
import sys
import tempfile
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("py_modules", "tests", "tools"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import croteam_fixture as F  # noqa: E402
from button_glyphs import croteam_tex, engine, gro, image, overlay_patch  # noqa: E402
from button_glyphs.profiles import PROFILES, serious_sam_2 as ss2, serious_tables  # noqa: E402
from button_glyphs.profiles.overlay_glyphs import PACKAGE_DIR, opaque_box  # noqa: E402
from test_backend import GLYPH_DIR, make_library  # noqa: E402

TABLE = serious_tables.GAMES["204340"]
ROWS = {ss2.key_of(r): r for r in TABLE["textures"]}


def synthetic_original(key, clear):
    """A raw-BGRA .tex shaped like the real ones: an opaque oval, the game's colour under alpha 0 (the pad: 1024 px)."""
    if key == "controller":
        return F.build_se2(1024, 1024, bytes((20, 30, 40, 255)) * (1024 * 1024))
    px = bytearray()
    for y in range(128):
        for x in range(128):
            inside = ((x - 64) / 38.0) ** 2 + ((y - 63.5) / 46.5) ** 2 <= 1
            px += bytes((30, 60, 200, 255)) if inside else bytes(clear) + b"\0"
    return F.build_se2(128, 128, bytes(px))


class TableTest(unittest.TestCase):
    def test_the_entries_are_the_replace_rows_of_the_generated_table(self):
        keys = {e["key"] for e in ss2.PROFILE.entries}
        self.assertEqual(keys - {"controller"}, {k for k, r in ROWS.items() if r["action"] == "replace"})
        self.assertEqual(set(ss2.CELLS), keys - {"controller"})
        self.assertEqual(ROWS["controller"]["action"], "pad_art")

    def test_pins_come_from_the_table(self):
        for e in ss2.PROFILE.entries:
            r = ROWS[e["key"]]
            self.assertEqual((e["path"], e["size"], e["sha256"], e["gro"]), (r["path"], r["size"], r["sha256"], r["gro"]))
            self.assertEqual((e["width"], e["height"], r["fmt"]),
                             ((1024, 1024) if e["key"] == "controller" else (128, 128)) + ("bgra8",))
            self.assertEqual(len(e["sha256"]), 64)

    def test_rows_the_profile_keeps_are_not_in_it(self):
        for k, r in ROWS.items():
            if r["action"] in ("keep", "optional", "repoint_arrows"):
                self.assertNotIn(k, {e["key"] for e in ss2.PROFILE.entries})

    def test_our_archive_sorts_after_every_archive_of_the_supported_build(self):
        """The engine loads root .gro files alphabetically (the game's own log lists them so); ours must come last."""
        names = [n for n, _, _ in TABLE["load_order"]]
        ours = ss2.PROFILE.archive
        self.assertGreater(ours.lower(), max(n.lower() for n in names))
        self.assertGreater(ours, max(names))                      # also in plain byte order
        self.assertEqual(ss2.PROFILE.mount, TABLE["mount"])

    def test_every_winning_archive_is_in_the_load_order(self):
        names = {n for n, _, _ in TABLE["load_order"]}
        for e in ss2.PROFILE.entries:
            self.assertIn(e["gro"], names)

    def test_the_measured_boxes_are_the_ones_on_the_page_and_in_the_box_of_the_canvas(self):
        for k, c in ss2.CELLS.items():
            x0, y0, x1, y1 = c["box"]
            self.assertTrue(0 <= x0 < x1 <= 128 and 0 <= y0 < y1 <= 128, k)
            self.assertEqual((x1 - x0, y1 - y0 in (93, 94)), (76, True), k)

    def test_flags_roles_and_revision(self):
        p = ss2.PROFILE
        self.assertTrue(p.overlay_level)
        self.assertEqual((p.appid, p.native_style, p.patch_styles, p.vehicle, p.support), ("204340", "xbox", ("ps",), "gro", "partial"))
        self.assertGreaterEqual(p.revision, 1)
        self.assertEqual(len(p.roles), 6)
        self.assertEqual(p.revision, 2)
        self.assertIn("unconfirmed", " ".join(l for _, l in p.roles))
        self.assertTrue(p.original_hint and p.notes)

    def test_it_is_registered(self):
        self.assertIs(PROFILES["204340"], ss2.PROFILE)


class BundledArtTest(unittest.TestCase):
    """The REAL pictures the profile draws with."""

    def test_every_picture_exists_and_is_a_128_px_rgba_icon(self):
        for k, c in ss2.CELLS.items():
            rgba, w, h = image.read_png(os.path.join(PACKAGE_DIR, c["art"] + ".png"))
            self.assertEqual((w, h), (128, 128), k)
            self.assertIsNotNone(opaque_box(rgba, w, h), k)

    def test_preflight_accepts_the_real_art(self):
        ss2.make_profile().preflight()

    def test_the_pad_picture_is_the_size_of_the_original_pad_texture(self):
        rgba, w, h = image.read_png(os.path.join(PACKAGE_DIR, ss2.PAD_PICTURE_ART + ".png"))
        self.assertEqual((w, h), (ROWS["controller"]["width"], ROWS["controller"]["height"]))
        self.assertTrue(any(rgba[i] == 255 for i in range(3, len(rgba), 4)))
        self.assertTrue(any(rgba[i] == 0 for i in range(3, len(rgba), 4)))
        ss2.make_profile(pad_picture=True).preflight()

    def test_the_pad_picture_is_on_by_default_and_the_flag_turns_it_off(self):
        e = next(x for x in ss2.PROFILE.entries if x["key"] == "controller")
        self.assertEqual((e["width"], e["height"]), (1024, 1024))
        self.assertNotIn("controller", {x["key"] for x in ss2.make_profile(pad_picture=False).entries})

    def test_the_arrow_pictures_the_glow_and_the_layout_are_never_touched(self):
        names = {e["key"] for e in ss2.PROFILE.entries}
        for k, r in ROWS.items():
            if r["kind"] in ("pad_arrow", "layout", "dpad", "stick"):
                self.assertNotIn(k, names)
        self.assertFalse([e for e in ss2.PROFILE.entries if "Glow" in e["key"] or e["path"].endswith(".rsc")])

    def test_shoulders_use_the_solid_variants(self):
        self.assertTrue(ss2.CELLS["Use_button"]["art"].endswith("plain-rectangle-R1"))
        self.assertTrue(ss2.CELLS["White_button"]["art"].endswith("plain-rectangle-L1"))


class DrawingTest(unittest.TestCase):
    """The real art on synthetic originals of the real size: shape, place, convention, orientation."""

    @classmethod
    def setUpClass(cls):
        cls.originals, cls.entries = {}, []
        for e in ss2.PROFILE.entries:
            data = synthetic_original(e["key"], ss2.CELLS.get(e["key"], {"clear": (0, 0, 0)})["clear"])
            cls.originals[e["path"]] = data
            cls.entries.append(dict(e, size=len(data), sha256=overlay_patch.sha256(data),
                                crc32="%08x" % (zlib.crc32(data) & 0xFFFFFFFF)))
        cls.profile = ss2.make_profile(entries=cls.entries)
        cls.outputs = cls.profile.outputs("ps", cls.originals)

    def pixels(self, key):
        e = next(x for x in self.entries if x["key"] == key)
        return croteam_tex.decode(self.outputs[e["path"]])[0]

    def test_each_symbol_is_round_and_sits_on_the_centre_of_the_original_oval(self):
        for key, c in ss2.CELLS.items():
            box = opaque_box(self.pixels(key), 128, 128)
            bx0, by0, bx1, by1 = c["box"]
            cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
            self.assertLessEqual(box[2] - box[0], 76, key)
            self.assertLessEqual(box[3] - box[1], 76, key)
            self.assertAlmostEqual((box[0] + box[2]) / 2, cx, delta=1.0, msg=key)
            self.assertAlmostEqual((box[1] + box[3]) / 2, cy, delta=1.0, msg=key)
        for key in ("A_button", "B_button", "X_button", "Y_button"):     # the four ring symbols fill the circle
            box = opaque_box(self.pixels(key), 128, 128)
            self.assertEqual((box[2] - box[0], box[3] - box[1]), (76, 76), key)

    def test_the_margins_of_the_original_are_kept_transparent(self):
        for key in ss2.CELLS:
            px = self.pixels(key)
            for y in range(128):
                for x in list(range(0, 24)) + list(range(104, 128)):
                    self.assertEqual(px[(y * 128 + x) * 4 + 3], 0, (key, x, y))

    def test_transparent_pixels_keep_the_colour_the_game_stores_under_alpha_zero(self):
        for key, c in ss2.CELLS.items():
            px = self.pixels(key)
            for i in range(0, len(px), 4):
                if px[i + 3] == 0:
                    self.assertEqual(tuple(px[i:i + 3]), c["clear"], key)
        self.assertEqual(ss2.CELLS["White_button"]["clear"], (255, 255, 255))

    def test_the_symbols_are_the_ps_colours(self):
        """cross blue, circle red, square pink, triangle green (the outline art's own colours)."""
        want = {"A_button": "b", "B_button": "r", "X_button": "p", "Y_button": "g"}
        for key, kind in want.items():
            px = self.pixels(key)
            n = {"r": 0, "g": 0, "b": 0, "p": 0}
            for i in range(0, len(px), 4):
                r, g, b, a = px[i:i + 4]
                if a > 200:
                    if r > 150 and b > 150 and g < r - 40:
                        n["p"] += 1
                    elif b > r and b > g:
                        n["b"] += 1
                    elif r > g and r > b:
                        n["r"] += 1
                    elif g > r and g >= b - 40:
                        n["g"] += 1
            self.assertEqual(max(n, key=n.get), kind, (key, n))

    def test_a_triangle_points_up_in_game_orientation(self):
        """The one asymmetric symbol: inside the ring the base is wider than the apex (rows are top-down, no flip)."""
        px = self.pixels("Y_button")
        top = bottom = 0
        for y in range(128):
            for x in range(64 - 22, 64 + 22):
                if px[(y * 128 + x) * 4 + 3] > 200 and 38 < y < 90:
                    if y < 64:
                        top += 1
                    else:
                        bottom += 1
        self.assertGreater(bottom, top)

    def test_the_six_pictures_are_different_and_the_originals_are_replaced(self):
        outs = list(self.outputs.values())
        self.assertEqual(len(set(outs)), len(outs))
        for e in self.entries:
            self.assertNotEqual(self.outputs[e["path"]], self.originals[e["path"]])

    def test_only_the_pixel_block_changes(self):
        for e in self.entries:
            a, b = croteam_tex.parse(self.originals[e["path"]]), croteam_tex.parse(self.outputs[e["path"]])
            self.assertEqual((a.head, a.tail, a.width, a.height, a.fmt), (b.head, b.tail, b.width, b.height, b.fmt))

    def test_the_diameter_is_one_constant_to_tune(self):
        small = ss2.make_profile(entries=self.entries)
        small.builders["A_button"].diameter = 50
        e = next(x for x in self.entries if x["key"] == "A_button")
        px = croteam_tex.decode(small.builders["A_button"].build(self.originals[e["path"]], e, "ps", small.pictures))[0]
        box = opaque_box(px, 128, 128)
        self.assertEqual((box[2] - box[0], box[3] - box[1]), (50, 50))


class RealProfileThroughTheEngineTest(unittest.TestCase):
    """The real profile (real table geometry, real art) on a synthetic game folder whose entries carry the real paths."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        common = make_library(self.home, [("204340", "Serious Sam 2", "Serious Sam 2", "4")])
        self.install = os.path.join(common, "Serious Sam 2")
        os.makedirs(self.install, exist_ok=True)
        self.originals, entries = {}, []
        for e in ss2.PROFILE.entries:
            data = synthetic_original(e["key"], ss2.CELLS.get(e["key"], {"clear": (0, 0, 0)})["clear"])
            self.originals[e["path"]] = data
            entries.append(dict(e, size=len(data), sha256=overlay_patch.sha256(data),
                                crc32="%08x" % (zlib.crc32(data) & 0xFFFFFFFF)))
        # the load order of the real build: every real archive name exists; the winner holds the six textures
        for name, _, _ in TABLE["load_order"]:
            entries_in = [(e["path"], self.originals[e["path"]]) for e in ss2.PROFILE.entries] \
                if name == "Patch_02_100_800000.gro" else [("Content/x/%s.bin" % name, name.encode())]
            with open(os.path.join(self.install, name), "wb") as f:
                f.write(gro.build(entries_in, deflate=True))
        self.profile = ss2.make_profile(entries=entries)
        self.proc = os.path.join(self.home, "proc")
        os.makedirs(self.proc)
        self.eng = engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={"204340": self.profile}, proc_root=self.proc)

    def test_ps_adds_zz_ButtonGlyphs_and_original_removes_it(self):
        before = sorted(os.listdir(self.install))
        res = self.eng.set_style("204340", "ps")
        self.assertTrue(res["ok"], res)
        after = sorted(os.listdir(self.install))
        self.assertEqual(sorted(set(after) - set(before)), ["zz_ButtonGlyphs.gro"])
        self.assertEqual(sorted(after)[-1], "zz_ButtonGlyphs.gro")       # last in the load order
        names = [n for n, _, _, _ in gro.list_entries(os.path.join(self.install, "zz_ButtonGlyphs.gro"))]
        self.assertEqual(sorted(names), sorted(e["path"] for e in self.profile.entries))
        st = next(g for g in self.eng.list_games() if g["appid"] == "204340")
        self.assertEqual((st["state"], st["applied"], st["support"]), ("ok", "ps", "partial"))
        self.assertTrue(self.eng.set_style("204340", "original")["ok"])
        self.assertEqual(sorted(os.listdir(self.install)), before)


if __name__ == "__main__":
    unittest.main()
