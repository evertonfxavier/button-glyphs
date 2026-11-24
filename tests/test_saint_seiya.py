"""Saint Seiya: Soldiers' Soul profile: DXT5 cells redrawn inside whole-texture regions."""
import hashlib
import os
import shutil
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from button_glyphs import image, region_patch  # noqa: E402
from button_glyphs.profiles import saint_seiya  # noqa: E402
from button_glyphs.profiles.dxt_regions import HEADER, DxtRegionProfile  # noqa: E402

PROFILE = saint_seiya.PROFILE
SOLID_GREY = image.encode_block([(120, 120, 120, 255)] * 16, "DXT5")


def dds(w, h, block=SOLID_GREY):
    """A single-level DXT5 DDS with a zeroed reserved area, every block `block`."""
    head = bytearray(HEADER)
    head[0:4] = b"DDS "
    struct.pack_into("<IIIII", head, 4, 124, 0x81007, h, w, w * h)
    head[84:88] = b"DXT5"
    return bytes(head) + block * (w // 4 * (h // 4))


def blocks_changed(a, b):
    return {(i - HEADER) // 16 for i in range(HEADER, len(a), 16) if a[i:i + 16] != b[i:i + 16]}


class TableTest(unittest.TestCase):
    def test_regions_are_consistent(self):
        regions = PROFILE.regions
        self.assertEqual(len(regions), 34)
        self.assertEqual(len({r["key"] for r in regions}), 34)
        spans = sorted((r["offset"], r["offset"] + r["size"]) for r in regions)
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertLessEqual(end, start)
        for r in regions:
            self.assertEqual(r["size"], HEADER + r["width"] * r["height"], r["key"])
            self.assertIn(r["layout"], PROFILE.layouts)
            self.assertEqual(len(r["sha256"]), 64)

    def test_every_language_is_covered(self):
        langs = {r["key"].split("/")[0] for r in PROFILE.regions}
        self.assertEqual(langs, {"BR", "CH", "EN", "FR", "IT", "JP", "NS", "SP"})

    def test_cells_stay_inside_their_texture_and_do_not_overlap(self):
        sizes = {r["layout"]: (r["width"], r["height"]) for r in PROFILE.regions}
        for name, cells in PROFILE.layouts.items():
            w, h = sizes[name]
            windows = []
            for c in cells:
                x0, y0, x1, y1 = c["clear"]
                win = (x0 // 4, y0 // 4, x1 // 4 + 1, y1 // 4 + 1)
                self.assertTrue(0 <= win[0] < win[2] <= w // 4 and 0 <= win[1] < win[3] <= h // 4,
                                "%s %s" % (name, c["clear"]))
                for rect, icon in c["draws"]:
                    self.assertTrue(x0 <= rect[0] and rect[2] <= x1 and y0 <= rect[1] and rect[3] <= y1)
                    self.assertTrue(os.path.exists(os.path.join(PROFILE.glyph_dir, icon + ".png")), icon)
                for o in windows:
                    self.assertTrue(win[2] <= o[0] or o[2] <= win[0] or win[3] <= o[1] or o[3] <= win[1],
                                    "%s: windows overlap" % name)
                windows.append(win)


class RenderTest(unittest.TestCase):
    def render(self, layout, w, h):
        original = dds(w, h)
        region = {"key": layout, "size": len(original), "width": w, "height": h, "layout": layout}
        return original, PROFILE.render(original, region, "ps")

    def test_pad_layout_changes_only_cell_blocks(self):
        original, new = self.render("pad", 1024, 1024)
        self.assertEqual(len(new), len(original))
        self.assertEqual(new[:HEADER], original[:HEADER])
        changed = blocks_changed(original, new)
        row = 1024 // 4
        allowed = set()
        for c in PROFILE.layouts["pad"]:
            x0, y0, x1, y1 = c["clear"]
            allowed |= {by * row + bx for by in range(y0 // 4, y1 // 4 + 1)
                        for bx in range(x0 // 4, x1 // 4 + 1)}
        self.assertTrue(changed and changed <= allowed)
        # the face buttons are drawn: coloured pixels where the circle goes
        rgba = image.decode(new[HEADER:], 1024, 1024, "DXT5")
        px = [rgba[(y * 1024 + x) * 4:(y * 1024 + x) * 4 + 4] for y in range(12, 56) for x in range(12, 56)]
        self.assertTrue(any(p[3] > 200 and p[0] > 150 and p[1] < 120 for p in px), "no red circle")
        # a cell nobody asked for is untouched (the L stick at 268,12 keeps its grey blocks)
        self.assertNotIn(12 * row + 268 // 4, changed)

    def test_cockpit_and_continue_layouts_render(self):
        for layout, w, h in (("cockpit", 1024, 1024), ("continue", 2048, 1024)):
            original, new = self.render(layout, w, h)
            self.assertTrue(blocks_changed(original, new), layout)

    def test_rejects_wrong_size_and_style(self):
        original = dds(1024, 1024)
        region = {"key": "k", "size": len(original), "width": 1024, "height": 1024, "layout": "pad"}
        with self.assertRaises(ValueError):
            PROFILE.render(original, region, "xbox")
        with self.assertRaises(ValueError):
            PROFILE.render(original[:-16], region, "ps")


class PatchRoundTripTest(unittest.TestCase):
    """region_patch on a fake archive: patch, detect, revert byte-for-byte."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.install = os.path.join(self.dir, "game")
        os.makedirs(self.install)
        self.backup = os.path.join(self.dir, "backup")
        tex = dds(64, 64)
        junk = bytes(range(256)) * 40
        self.regions = []
        data = bytearray(junk)
        for key in ("EN/a", "JP/a"):
            self.regions.append({"key": key, "offset": len(data), "size": len(tex), "width": 64,
                                 "height": 64, "sha256": hashlib.sha256(tex).hexdigest(),
                                 "layout": "t"})
            data += tex + junk
        with open(os.path.join(self.install, "pack.cpk"), "wb") as f:
            f.write(data)
        self.original = bytes(data)
        layouts = {"t": [{"clear": (8, 8, 39, 39), "draws": [((8, 8, 39, 39), "ps/outline-red-circle")]}]}
        self.profile = DxtRegionProfile(
            appid="1", name="t", file="pack.cpk", regions=self.regions, layouts=layouts,
            roles=[], native_style="xbox")

    def tearDown(self):
        shutil.rmtree(self.dir)

    def current(self):
        with open(os.path.join(self.install, "pack.cpk"), "rb") as f:
            return f.read()

    def test_apply_detect_revert(self):
        rows = region_patch.plan(self.profile, self.install, "ps")
        self.assertFalse(any(r[3] for r in rows))
        region_patch.ensure_backups(self.profile, self.install, self.backup, rows)
        self.assertEqual(region_patch.check(self.profile, self.install, self.backup, rows), [])
        region_patch.apply(self.profile, self.install, self.backup, rows, "ps")

        patched = self.current()
        self.assertEqual(len(patched), len(self.original))
        rows = region_patch.plan(self.profile, self.install, "ps")
        self.assertTrue(all(r[3] for r in rows))
        self.assertEqual(region_patch.applied_style(rows), "ps")
        # bytes outside the regions are identical
        for r in self.regions:
            self.assertEqual(patched[:r["offset"]][-256:], self.original[:r["offset"]][-256:])
        self.assertEqual(patched[-10240:], self.original[-10240:])

        region_patch.revert(self.profile, self.install, self.backup, rows)
        self.assertEqual(self.current(), self.original)

    def test_unknown_texture_is_refused(self):
        with open(os.path.join(self.install, "pack.cpk"), "r+b") as f:
            f.seek(self.regions[0]["offset"] + HEADER + 16)
            f.write(b"\x01" * 16)
        rows = region_patch.plan(self.profile, self.install, "ps")
        problems = region_patch.check(self.profile, self.install, self.backup, rows)
        self.assertTrue(problems and "differs" in problems[0])


class EngineTest(unittest.TestCase):
    """The engine drives the profile: apply, switch to the native style, restore."""

    def setUp(self):
        from button_glyphs import engine
        from test_backend import GLYPH_DIR, make_library
        self.home = tempfile.mkdtemp()
        common = make_library(self.home, [("348710", "Saint Seiya", "SSSS", "4")])
        install = os.path.join(common, "SSSS")
        tex = dds(64, 64)
        data = bytearray(os.urandom(5000))
        off = len(data)
        data += tex + os.urandom(3000)
        regions = [{"key": "EN/a", "offset": off, "size": len(tex), "width": 64, "height": 64,
                    "sha256": hashlib.sha256(tex).hexdigest(), "layout": "t"}]
        self.path = os.path.join(install, "localize.cpk")
        with open(self.path, "wb") as f:
            f.write(data)
        self.pristine = bytes(data)
        layouts = {"t": [{"clear": (8, 8, 39, 39), "draws": [((8, 8, 39, 39), "ps/outline-blue-cross")]}]}
        profile = DxtRegionProfile(
            appid="348710", name="t", file="localize.cpk", regions=regions, layouts=layouts,
            roles=[], native_style="xbox")
        self.profile = profile
        proc = os.path.join(self.home, "proc")
        os.makedirs(proc)
        self.eng = engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={"348710": profile}, proc_root=proc)

    def tearDown(self):
        shutil.rmtree(self.home)

    def data(self):
        with open(self.path, "rb") as f:
            return f.read()

    def status(self):
        return next(g for g in self.eng.list_games() if g["appid"] == "348710")

    def test_ps_then_xbox_then_ps_then_original(self):
        self.assertEqual(self.status()["applied"], "xbox")
        self.assertTrue(self.eng.set_style("348710", "ps")["ok"])
        self.assertNotEqual(self.data(), self.pristine)
        self.assertEqual(len(self.data()), len(self.pristine))
        st = self.status()
        self.assertEqual((st["state"], st["applied"]), ("ok", "ps"))
        # Xbox is what the game ships: choosing it restores the original bytes
        self.assertTrue(self.eng.set_style("348710", "xbox")["ok"])
        self.assertEqual(self.data(), self.pristine)
        self.assertEqual(self.status()["applied"], "xbox")
        self.assertTrue(self.eng.set_style("348710", "ps")["ok"])
        self.assertTrue(self.eng.set_style("348710", "original")["ok"])
        self.assertEqual(self.data(), self.pristine)

    def test_reapplied_after_steam_restores_the_file(self):
        self.eng.set_style("348710", "ps")
        with open(self.path, "wb") as f:       # "Verify integrity of game files"
            f.write(self.pristine)
        self.assertEqual(self.status()["state"], "pending")
        results = self.eng.sync_all("watch")
        self.assertTrue(results and results[0]["changed"], results)
        self.assertEqual(self.status()["applied"], "ps")

    def test_refusal_shows_the_error_not_pending(self):
        off = self.profile.regions[0]["offset"]
        with open(self.path, "r+b") as f:
            f.seek(off + HEADER + 16)
            f.write(b"\x01" * 16)               # another build of the texture
        before = self.data()
        self.assertFalse(self.eng.set_style("348710", "ps")["ok"])
        self.assertEqual(self.data(), before)
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertIn("not supported", st["message"])

    def test_missing_icon_fails_before_the_first_write(self):
        self.profile.glyph_dir = os.path.join(self.home, "no-glyphs")
        r = self.eng.set_style("348710", "ps")
        self.assertFalse(r["ok"])
        self.assertIn("icon", r["errors"][0])
        self.assertEqual(self.data(), self.pristine)
        self.assertNotIn(region_patch.MAGIC, self.data())
        self.assertEqual(self.status()["state"], "error")


if __name__ == "__main__":
    unittest.main()
