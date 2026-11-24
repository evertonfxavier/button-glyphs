"""tools/sfh_art.py and tools/art_overlap.py: sheet slicing, footprint fitting, hard alpha, native size.

Synthetic input only (a tiny made-up sheet and table): no game pixels and no user art. Pillow and numpy are
dev-only dependencies of the tools, so the whole module is skipped where they are missing."""
import importlib.util
import os
import sys
import tempfile
import unittest

try:
    import numpy as np
    from PIL import Image
except ImportError:                                            # the backend suite is stdlib only
    np = Image = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if np is not None:
    sfh_art = load_tool("sfh_art")
    art_overlap = load_tool("art_overlap")

BODY = (50, 50, 56)
RED = (230, 60, 50)


def disc_icon(rgba, x0, y0, size, symbol=RED):
    """A grey square with a black rim and a red ring in the middle (a stand-in for a face button)."""
    x1, y1 = x0 + size, y0 + size
    rgba[y0:y1, x0:x1] = BODY + (255,)
    rim = max(2, size // 10)
    rgba[y0:y0 + rim, x0:x1] = (0, 0, 0, 255)
    rgba[y1 - rim:y1, x0:x1] = (0, 0, 0, 255)
    rgba[y0:y1, x0:x0 + rim] = (0, 0, 0, 255)
    rgba[y0:y1, x1 - rim:x1] = (0, 0, 0, 255)
    a, b, t = x0 + size // 4, x0 + size * 3 // 4, max(2, size // 10)
    ya, yb = y0 + size // 4, y0 + size * 3 // 4
    for sl in ((slice(ya, ya + t), slice(a, b)), (slice(yb - t, yb), slice(a, b)),
               (slice(ya, yb), slice(a, a + t)), (slice(ya, yb), slice(b - t, b))):
        rgba[sl] = symbol + (255,)


def make_sheet():
    """300x200: A and B in row 1 (B has a small ray beside it), C in row 2; low-alpha fringe and a speck."""
    sheet = np.zeros((200, 300, 4), dtype=np.uint8)
    disc_icon(sheet, 10, 10, 60)                                # A  60x60
    disc_icon(sheet, 110, 10, 60)                               # B  60x60 ...
    sheet[20:28, 178:186] = (255, 186, 0, 255)                  # ... plus a ray 8 px away (joins B)
    sheet[120:180, 10:90] = (90, 90, 100, 255)                  # C  80x60 plain slab
    sheet[5:9, 250:254] = (0, 0, 0, 10)                         # fringe/speck: alpha 10, dropped
    sheet[100:104, 200:204] = (0, 0, 0, 40)                     # alpha 40 < 64, dropped too
    return sheet


TABLE = {
    "Tex/Face": {"width": 20, "height": 16, "cells": [{"rect": (4, 2, 15, 13)}]},       # 12x12 footprint
    "Tex/Ray": {"width": 24, "height": 16, "cells": [{"rect": (2, 2, 13, 13)}, {"rect": (16, 6, 20, 9)}]},
    "Tex/Slab": {"width": 20, "height": 16, "cells": [{"rect": (0, 3, 19, 12)}]},       # 20x10 footprint
}
TARGETS = [("Tex/Face", "A", None), ("Tex/Ray", "B", "left"), ("Tex/Slab", "C", None)]


@unittest.skipIf(np is None, "numpy/Pillow not installed (dev-only tools)")
class LabelTest(unittest.TestCase):
    def test_eight_connectivity_joins_diagonals_four_does_not(self):
        m = np.zeros((6, 8), dtype=bool)
        m[1, 1] = m[2, 2] = True                                 # diagonal pair
        m[4, 5] = m[4, 6] = True                                 # separate pair
        l8, n8 = sfh_art.label_components(m, 8)
        l4, n4 = sfh_art.label_components(m, 4)
        self.assertEqual((n8, n4), (2, 3))
        self.assertEqual(l8[1, 1], l8[2, 2])
        self.assertNotEqual(l8[1, 1], l8[4, 5])
        self.assertEqual(l8[0, 0], 0)

    def test_u_shape_is_one_component(self):
        m = np.zeros((5, 5), dtype=bool)
        m[:, 0] = m[:, 4] = True
        m[4, :] = True                                           # the two arms meet only at the bottom
        labels, n = sfh_art.label_components(m)
        self.assertEqual(n, 1)
        self.assertEqual(set(np.unique(labels)), {0, 1})

    def test_empty_mask(self):
        labels, n = sfh_art.label_components(np.zeros((3, 3), dtype=bool))
        self.assertEqual(n, 0)
        self.assertFalse(labels.any())


@unittest.skipIf(np is None, "numpy/Pillow not installed (dev-only tools)")
class SliceTest(unittest.TestCase):
    def test_names_order_sizes_and_fringe(self):
        icons = sfh_art.slice_sheet(make_sheet(), names=["A", "B", "C"], row_counts=(2, 1))
        self.assertEqual([i.name for i in icons], ["A", "B", "C"])
        a, b, c = icons
        self.assertEqual(a.size, (60, 60))
        self.assertEqual(c.size, (80, 60))
        self.assertEqual(b.parts, 2)                             # the ray joined B
        self.assertEqual(b.box, (110, 10, 185, 69))
        self.assertEqual(int(b.body.sum()), 60 * 60)             # body = the big piece only
        self.assertGreater(int(b.mask.sum()), int(b.body.sum()))
        self.assertEqual(a.parts, 1)

    def test_alpha_below_threshold_is_dropped(self):
        sheet = make_sheet()
        icons = sfh_art.slice_sheet(sheet, names=["A", "B", "C"], row_counts=(2, 1))
        total = sum(int(i.mask.sum()) for i in icons)
        self.assertEqual(total, int((sheet[..., 3] >= sfh_art.ALPHA_MIN).sum()))

    def test_wrong_icon_count_is_refused(self):
        with self.assertRaises(sfh_art.SheetError):
            sfh_art.slice_sheet(make_sheet(), names=["A", "B", "C", "D"], row_counts=(2, 2))

    def test_wrong_row_layout_is_refused(self):
        with self.assertRaises(sfh_art.SheetError):
            sfh_art.slice_sheet(make_sheet(), names=["A", "B", "C"], row_counts=(1, 2))

    def test_far_stray_speck_above_threshold_stops_the_run(self):
        sheet = make_sheet()
        sheet[190:194, 290:294] = (255, 255, 255, 255)           # opaque speck, 100+ px from any icon
        with self.assertRaises(sfh_art.SheetError):
            sfh_art.slice_sheet(sheet, names=["A", "B", "C"], row_counts=(2, 1))


@unittest.skipIf(np is None, "numpy/Pillow not installed (dev-only tools)")
class FitTest(unittest.TestCase):
    def test_union_rect(self):
        self.assertEqual(sfh_art.union_rect(TABLE["Tex/Ray"]["cells"]), (2, 2, 20, 13))

    def test_union_rect_of_one_kind_and_grown(self):
        cells = [{"kind": "pill", "rect": (8, 2, 19, 10)}, {"kind": "label", "rect": (4, 13, 22, 19)}]
        self.assertEqual(sfh_art.union_rect(cells), (4, 2, 22, 19))
        self.assertEqual(sfh_art.union_rect(cells, kind="pill", grow=1), (7, 1, 20, 11))

    def test_fixed_size_is_used_and_must_fit(self):
        icons = sfh_art.slice_sheet(make_sheet(), names=["A", "B", "C"], row_counts=(2, 1))
        tex, rect = sfh_art.render_texture(icons[0], 20, 16, (4, 2, 15, 13), size=(9, 7))
        self.assertEqual(tex.shape, (16, 20, 4))
        self.assertEqual((rect[2] - rect[0] + 1, rect[3] - rect[1] + 1), (9, 7))
        with self.assertRaises(ValueError):
            sfh_art.render_texture(icons[0], 20, 16, (4, 2, 15, 13), size=(13, 7))

    def test_start_is_the_icon_part_only_at_a_fixed_size(self):
        table = sfh_art.load_table()
        fp = sfh_art.union_rect(table["Tooltip/START"]["cells"], **sfh_art.ICON_PART["Tooltip/START"])
        self.assertEqual(fp, (7, 1, 20, 11))
        w, h = sfh_art.FIXED_SIZE["Tooltip/START"]
        self.assertTrue(w <= fp[2] - fp[0] + 1 and h <= fp[3] - fp[1] + 1)

    def test_fit_size_stays_inside_and_keeps_aspect(self):
        for iw, ih, fw, fh in ((130, 132, 11, 11), (180, 91, 15, 17), (164, 190, 17, 21), (109, 87, 19, 18)):
            w, h = sfh_art.fit_size(iw, ih, fw, fh)
            self.assertTrue(1 <= w <= fw and 1 <= h <= fh)
            self.assertLessEqual(abs(float(w) / h - float(iw) / ih) / (float(iw) / ih), 0.2)

    def test_fit_size_prefers_an_exactly_centred_size(self):
        # exact 16.4 x 17: 17 wide centres exactly in a 17-wide footprint, 16 would sit half a pixel off
        self.assertEqual(sfh_art.fit_size(164, 170, 17, 17), (17, 17))

    def test_place_centres_and_anchors(self):
        icon = np.full((4, 6, 4), 255, dtype=np.uint8)
        canvas, rect = sfh_art.place(icon, 20, 16, (4, 2, 15, 13))
        self.assertEqual(rect, (7, 6, 12, 9))
        self.assertEqual(canvas.shape, (16, 20, 4))
        self.assertEqual(sfh_art.place(icon, 20, 16, (4, 2, 15, 13), "left")[1][0], 4)
        self.assertEqual(sfh_art.place(icon, 20, 16, (4, 2, 15, 13), "right")[1][2], 15)
        self.assertEqual(sfh_art.place(icon, 20, 16, (4, 2, 15, 13), "bottom")[1][3], 13)
        self.assertEqual(sfh_art.place(icon, 20, 16, (4, 2, 15, 13), "top")[1][1], 2)

    def test_icon_that_leaves_the_texture_is_refused(self):
        with self.assertRaises(ValueError):
            sfh_art.place(np.zeros((4, 30, 4), dtype=np.uint8), 20, 16, (0, 0, 19, 15))


@unittest.skipIf(np is None, "numpy/Pillow not installed (dev-only tools)")
class PixelTest(unittest.TestCase):
    def test_snap_alpha_is_hard_and_clears_transparent_colour(self):
        px = np.array([[[10, 20, 30, 127], [10, 20, 30, 128], [200, 100, 50, 0], [1, 2, 3, 255]]], dtype=np.uint8)
        out = sfh_art.snap_alpha(px)
        self.assertEqual(out[0, :, 3].tolist(), [0, 255, 0, 255])
        self.assertEqual(out[0, 0, :3].tolist(), [0, 0, 0])      # transparent = (0,0,0,0)
        self.assertEqual(out[0, 2].tolist(), [0, 0, 0, 0])
        self.assertEqual(out[0, 1, :3].tolist(), [10, 20, 30])

    def test_clean_removes_lonely_pixel_and_fills_pinhole(self):
        px = np.zeros((7, 9, 4), dtype=np.uint8)
        px[1, 7] = (255, 0, 0, 255)                              # lonely
        px[2:6, 1:5] = (40, 40, 40, 255)
        px[3, 2] = 0                                             # pinhole inside the block
        out = sfh_art.clean(px)
        self.assertEqual(out[1, 7].tolist(), [0, 0, 0, 0])
        self.assertEqual(out[3, 2].tolist(), [40, 40, 40, 255])

    def test_area_resize_is_exact_for_whole_factors_and_keeps_mass(self):
        a = np.arange(16, dtype=float).reshape(4, 4)
        self.assertEqual(sfh_art.area_resize(a, 2, 2).tolist(), [[2.5, 4.5], [10.5, 12.5]])
        b = np.random.RandomState(1).rand(13, 17)
        self.assertAlmostEqual(float(sfh_art.area_resize(b, 5, 7).mean()), float(b.mean()), places=9)

    def test_set_label_bitmap(self):
        bits = sfh_art.set_label("L1")
        self.assertEqual(bits.shape, (5, 7))                     # 3 + gap + 3 wide, 5 high
        self.assertEqual(bits[:, 0].tolist(), [True] * 5)        # the L's stem
        self.assertFalse(bits[:, 3].any())                       # the gap


@unittest.skipIf(np is None, "numpy/Pillow not installed (dev-only tools)")
class BuildTest(unittest.TestCase):
    def setUp(self):
        self.icons = sfh_art.slice_sheet(make_sheet(), names=["A", "B", "C"], row_counts=(2, 1))
        self.results = sfh_art.build(self.icons, TABLE, TARGETS, {"Tex/Slab": {"label": "L1"}})

    def by_key(self, key):
        return next(r for r in self.results if r["key"] == key)

    def test_output_is_exactly_the_texture_size_with_hard_alpha(self):
        for r in self.results:
            for v in r["variants"].values():
                self.assertEqual(v.shape, (r["height"], r["width"], 4), r["key"])
                self.assertEqual(v.dtype, np.uint8)
                self.assertTrue(set(np.unique(v[..., 3]).tolist()) <= {0, 255}, r["key"])
                self.assertFalse(v[v[..., 3] == 0].any(), "transparent pixels must be (0,0,0,0)")

    def test_icon_stays_inside_the_footprint_and_nothing_else_is_drawn(self):
        for r in self.results:
            fx0, fy0, fx1, fy1 = r["footprint"]
            ys, xs = np.nonzero(r["variants"]["A"][..., 3])
            self.assertTrue(xs.min() >= fx0 and xs.max() <= fx1 and ys.min() >= fy0 and ys.max() <= fy1, r["key"])
            x0, y0, x1, y1 = r["rect"]
            self.assertTrue(fx0 <= x0 <= x1 <= fx1 and fy0 <= y0 <= y1 <= fy1)

    def test_face_is_centred_and_fills_its_square_footprint(self):
        r = self.by_key("Tex/Face")
        self.assertEqual(r["rect"], (4, 2, 15, 13))               # 12x12 icon in a 12x12 footprint

    def test_anchor_left_keeps_the_pad_side(self):
        r = self.by_key("Tex/Ray")
        self.assertEqual(r["rect"][0], r["footprint"][0])

    def test_symbol_colour_survives_the_downscale(self):
        v = self.by_key("Tex/Face")["variants"]["A"].astype(int)
        opaque = v[v[..., 3] > 0][:, :3]
        red = opaque[(np.abs(opaque - np.array(RED)).max(axis=1) <= 12)]
        self.assertGreaterEqual(len(red), 8, "the thin red ring must keep its colour, not blend into the body")

    def test_variant_b_label_is_drawn_and_a_is_kept(self):
        r = self.by_key("Tex/Slab")
        self.assertEqual(sorted(r["variants"]), ["A", "B"])
        self.assertEqual(r["installed"], "B")
        self.assertFalse((r["variants"]["A"] == r["variants"]["B"]).all())
        self.assertEqual(self.by_key("Tex/Face")["installed"], "A")
        again = sfh_art.build(self.icons, TABLE, TARGETS, {"Tex/Slab": {"label": "L1"}}, policy="a")
        self.assertEqual(next(x for x in again if x["key"] == "Tex/Slab")["installed"], "A")

    def test_deterministic_arrays_and_png_bytes(self):
        again = sfh_art.build(sfh_art.slice_sheet(make_sheet(), names=["A", "B", "C"], row_counts=(2, 1)),
                              TABLE, TARGETS, {"Tex/Slab": {"label": "L1"}})
        for r1, r2 in zip(self.results, again):
            for name in r1["variants"]:
                self.assertTrue((r1["variants"][name] == r2["variants"][name]).all(), r1["key"])
        with tempfile.TemporaryDirectory() as d:
            p1, p2 = os.path.join(d, "1.png"), os.path.join(d, "2.png")
            sfh_art.save_png(p1, self.results[0]["variants"]["A"])
            sfh_art.save_png(p2, again[0]["variants"]["A"])
            with open(p1, "rb") as f1, open(p2, "rb") as f2:
                self.assertEqual(f1.read(), f2.read())

    def test_art_table_source(self):
        ns = {}
        exec(compile(sfh_art.art_source(self.results), "art", "exec"), ns)
        self.assertEqual(ns["ART"], {"Tex/Face": "sfh/Tex_Face", "Tex/Ray": "sfh/Tex_Ray", "Tex/Slab": "sfh/Tex_Slab"})
        self.assertIn("Generated by tools/sfh_art.py - do not edit", sfh_art.art_source(self.results))

    def test_manifest_lists_every_texture(self):
        m = sfh_art.manifest(self.results, None, self.icons)
        self.assertEqual([t["key"] for t in m["textures"]], ["Tex/Face", "Tex/Ray", "Tex/Slab"])
        self.assertEqual(len(m["icons"]), 3)


@unittest.skipIf(np is None, "numpy/Pillow not installed (dev-only tools)")
class RealTableTest(unittest.TestCase):
    """The shipped cell table and the shipped art agree (numbers only, no game data needed)."""

    def test_every_target_exists_in_the_table_with_a_footprint(self):
        table = sfh_art.load_table()
        self.assertEqual(len(sfh_art.TARGETS), 24)
        for key, icon, _anchor in sfh_art.TARGETS:
            self.assertIn(key, table)
            self.assertIn(icon, sfh_art.ICON_NAMES)
            fx0, fy0, fx1, fy1 = sfh_art.union_rect(table[key]["cells"])
            self.assertTrue(0 <= fx0 <= fx1 < table[key]["width"] and 0 <= fy0 <= fy1 < table[key]["height"], key)
        self.assertEqual(len(sfh_art.ICON_NAMES), 39)
        self.assertEqual(sum(sfh_art.ROW_COUNTS), 39)

    def test_installed_art_matches_the_table_sizes(self):
        table = sfh_art.load_table()
        path = os.path.join(ROOT, "py_modules", "button_glyphs", "profiles", "songs_for_a_hero_art.py")
        if not os.path.exists(path):
            self.skipTest("art not generated yet")
        ns = {}
        with open(path, encoding="utf-8") as f:
            exec(compile(f.read(), path, "exec"), ns)
        self.assertEqual(set(ns["ART"]), {k for k, _i, _a in sfh_art.TARGETS})
        for key, name in ns["ART"].items():
            im = Image.open(os.path.join(ROOT, "py_modules", "button_glyphs", "masks", name + ".png"))
            self.assertEqual(im.size, (table[key]["width"], table[key]["height"]), key)
            self.assertEqual(im.mode, "RGBA")
            alpha = np.array(im)[..., 3]
            self.assertTrue(set(np.unique(alpha).tolist()) <= {0, 255}, key)
            im.close()


@unittest.skipIf(np is None, "numpy/Pillow not installed (dev-only tools)")
class OverlapTest(unittest.TestCase):
    def test_identical_and_different_pictures(self):
        a = np.zeros((6, 6, 4), dtype=np.uint8)
        a[1:5, 1:5] = (200, 100, 50, 255)
        r = art_overlap.compare(a, a.copy())
        self.assertEqual((r["opaque"], r["same"]), (16, 16))
        self.assertEqual(r["identical_tiles"], r["tiles"])
        b = a.copy()
        b[1:5, 1:5] = (10, 20, 30, 255)
        r = art_overlap.compare(a, b)
        self.assertEqual((r["both"], r["same"], r["near"]), (16, 0, 0))
        self.assertEqual(r["identical_tiles"], 0)

    def test_transparent_colour_is_ignored_and_near_counts_small_differences(self):
        a = np.zeros((4, 4, 4), dtype=np.uint8)
        b = a.copy()
        a[0, 0] = (5, 5, 5, 0)                                   # transparent with junk colour
        a[2, 2] = (100, 100, 100, 255)
        b[2, 2] = (104, 98, 100, 255)
        r = art_overlap.compare(a, b)
        self.assertEqual((r["opaque"], r["same"], r["near"]), (1, 0, 1))

    def test_pairs_and_report_match_names_to_xnb_and_never_raise(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "content", "Tip"))
            os.makedirs(os.path.join(d, "cand"))
            Image.fromarray(np.zeros((3, 3, 4), dtype=np.uint8)).save(os.path.join(d, "cand", "Tip_A.png"))
            Image.fromarray(np.zeros((3, 3, 4), dtype=np.uint8)).save(os.path.join(d, "cand", "Tip_Missing.png"))
            with open(os.path.join(d, "content", "Tip", "A.xnb"), "wb") as f:
                f.write(b"not an xnb")                            # unreadable: reported, not raised
            rows = art_overlap.pairs(os.path.join(d, "cand"), os.path.join(d, "content"))
            self.assertEqual([r[0] for r in rows], ["Tip_A", "Tip_Missing"])
            self.assertIsNotNone(rows[0][2])
            self.assertIsNone(rows[1][2])
            text = art_overlap.report(rows)
            self.assertIn("error", text)
            self.assertIn("no matching .xnb", text)


if __name__ == "__main__":
    unittest.main()
