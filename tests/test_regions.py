"""Region-level patching: raw textures at fixed offsets inside a big archive (Quantum Break)."""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from button_glyphs import engine, region_patch  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402
from button_glyphs.profiles.region_glyphs import HEADER, RegionGlyphProfile  # noqa: E402
from test_backend import GLYPH_DIR, make_library  # noqa: E402

W = H = 64
APPID = "474960"
ARCHIVE = os.path.join("data", "ep999-000.rmdp")


def texture(seed):
    """A 64x64 BGRA atlas with a black face disc (letter hole) and a yellow trigger label."""
    px = bytearray(W * H * 4)
    for y in range(H):
        for x in range(W):
            o = (y * W + x) * 4
            if (x - 16) ** 2 + (y - 16) ** 2 <= 14 ** 2:           # disc, black, opaque
                px[o:o + 4] = bytes((0, 0, 0, 255))
            if 34 <= x <= 60 and 34 <= y <= 60:                    # label shape, yellow
                px[o:o + 4] = bytes((0, 195, 255, 255))
    for y in range(12, 21):                                         # a "letter" hole in the disc
        for x in range(12, 21):
            px[(y * W + x) * 4 + 3] = 0
    for y in range(44, 52):                                         # dark text on the label
        for x in range(40, 54):
            px[(y * W + x) * 4:(y * W + x) * 4 + 4] = bytes((0, 0, 0, 255))
    px[-4:] = bytes((seed, seed, seed, 255))                        # makes each texture unique
    return bytes(128 * [0]) + bytes(px)


def cells():
    return [
        {"kind": "disc", "role": "a", "rect": (2, 2, 30, 30), "clear": (9, 9, 23, 23),
         "draw": (10, 10, 22, 22), "fill": (0, 0, 0), "style": "hole", "color": (0, 0, 0)},
        {"kind": "trig", "role": "lt", "rect": (34, 34, 60, 60), "clear": (38, 42, 56, 54),
         "draw": (40, 44, 53, 51), "fill": (0, 195, 255), "style": "solid", "color": (0, 0, 0)},
    ]


def make_regions(textures, base=4096):
    out, off = [], base
    for i, tex in enumerate(textures):
        out.append({"key": "prompts/t%d" % i, "offset": off, "size": len(tex), "width": W,
                    "height": H, "sha256": hashlib.sha256(tex).hexdigest(), "cells": cells()})
        off += len(tex) + 1000
    return out, off


class RegionsTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        common = make_library(self.home, [(APPID, "Quantum Break", "QuantumBreak", "4")])
        self.install = os.path.join(common, "QuantumBreak")
        os.makedirs(os.path.join(self.install, "data"))
        self.textures = [texture(7), texture(9)]
        regions, end = make_regions(self.textures)
        self.regions = regions
        self.profile = RegionGlyphProfile(
            appid=APPID, name="Quantum Break", file=ARCHIVE, regions=regions,
            roles=[("a", "A / X")], native_style="xbox", revision=1)
        self.path = os.path.join(self.install, ARCHIVE)
        blob = bytearray(os.urandom(end + 5000))
        for r, t in zip(regions, self.textures):
            blob[r["offset"]:r["offset"] + len(t)] = t
        with open(self.path, "wb") as f:
            f.write(blob)
        self.pristine = bytes(blob)
        self.proc = os.path.join(self.home, "proc")
        os.makedirs(self.proc)
        self.eng = engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={APPID: self.profile}, proc_root=self.proc)

    def tearDown(self):
        shutil.rmtree(self.home)

    def data(self):
        with open(self.path, "rb") as f:
            return f.read()

    def status(self):
        return next(g for g in self.eng.list_games() if g["appid"] == APPID)

    def test_apply_changes_only_the_regions_and_keeps_size(self):
        r = self.eng.set_style(APPID, "ps")
        self.assertTrue(r["ok"], r)
        now = self.data()
        self.assertEqual(len(now), len(self.pristine))
        for reg in self.regions:
            o, s = reg["offset"], reg["size"]
            self.assertNotEqual(now[o:o + s], self.pristine[o:o + s])
        # every byte outside the regions is untouched
        mask = bytearray(len(now))
        for reg in self.regions:
            mask[reg["offset"]:reg["offset"] + reg["size"]] = b"\1" * reg["size"]
        self.assertTrue(all(a == b for a, b, m in zip(now, self.pristine, mask) if not m))
        st = self.status()
        self.assertEqual((st["state"], st["applied"]), ("ok", "ps"))

    def test_outside_the_cells_every_pixel_is_unchanged(self):
        self.eng.set_style(APPID, "ps")
        now = self.data()
        reg = self.regions[0]
        o = reg["offset"] + HEADER
        before, after = self.textures[0][HEADER:], now[o:o + W * H * 4]
        inside = set()
        for c in cells():
            x0, y0, x1, y1 = c["clear"]
            inside |= {(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)}
        for y in range(H):
            for x in range(W):
                if (x, y) not in inside:
                    i = (y * W + x) * 4
                    self.assertEqual(before[i:i + 4], after[i:i + 4], (x, y))

    def test_symbol_is_drawn_in_the_cell(self):
        self.eng.set_style(APPID, "ps")
        reg = self.regions[0]
        px = self.data()[reg["offset"] + HEADER:][:W * H * 4]
        # disc "hole" style: new glyph is transparent where the symbol is, black elsewhere
        alphas = {px[(y * W + x) * 4 + 3] for y in range(10, 23) for x in range(10, 23)}
        self.assertTrue(any(a < 128 for a in alphas) and 255 in alphas)
        # label: dark text on yellow -> some pixels stay yellow, some turn dark
        row = {tuple(px[(y * W + x) * 4:(y * W + x) * 4 + 3]) for y in range(44, 51)
               for x in range(40, 54)}
        self.assertIn((0, 195, 255), row)
        self.assertTrue(any(sum(c) < 120 for c in row))

    def test_restore_is_byte_exact(self):
        self.eng.set_style(APPID, "ps")
        self.assertNotEqual(self.data(), self.pristine)
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(self.data(), self.pristine)

    def test_native_style_restores_too(self):
        self.eng.set_style(APPID, "ps")
        self.assertTrue(self.eng.set_style(APPID, "xbox")["ok"])
        self.assertEqual(self.data(), self.pristine)
        st = self.status()
        self.assertEqual((st["state"], st["applied"]), ("ok", "xbox"))

    def test_marker_lives_in_the_dds_reserved_area(self):
        self.eng.set_style(APPID, "ps")
        reg = self.regions[0]
        hdr = self.data()[reg["offset"]:reg["offset"] + HEADER]
        self.assertTrue(hdr[0x20:0x4C].startswith(region_patch.MAGIC))
        self.assertEqual(hdr[:0x20], self.textures[0][:0x20])
        self.assertEqual(hdr[0x4C:], self.textures[0][0x4C:HEADER])

    def test_reapply_after_steam_restores_the_files(self):
        self.eng.set_style(APPID, "ps")
        patched = self.data()
        with open(self.path, "wb") as f:           # "verify files" / update puts originals back
            f.write(self.pristine)
        st = self.status()
        self.assertEqual(st["state"], "pending")
        results = self.eng.sync_all("watch")
        self.assertTrue(results[0]["changed"])
        self.assertEqual(self.data(), patched)     # deterministic output

    def test_partial_restore_only_redoes_what_is_missing(self):
        self.eng.set_style(APPID, "ps")
        patched = self.data()
        reg = self.regions[1]
        with open(self.path, "r+b") as f:
            f.seek(reg["offset"])
            f.write(self.textures[1])
        rows = region_patch.plan(self.profile, self.install, "ps")
        self.assertEqual([r[3] for r in rows], [True, False])
        self.eng.sync_all("watch")
        self.assertEqual(self.data(), patched)

    def test_unknown_texture_is_refused_and_left_alone(self):
        reg = self.regions[0]
        with open(self.path, "r+b") as f:
            f.seek(reg["offset"] + HEADER + 100)
            f.write(b"\xAB\xCD")                    # a different build of the texture
        before = self.data()
        r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("not supported", r["errors"][0])
        self.assertEqual(self.data(), before)

    def test_interrupted_write_is_recovered(self):
        self.eng.set_style(APPID, "ps")
        patched = self.data()
        reg = self.regions[0]
        with open(self.path, "r+b") as f:           # crash: marker says W, body half-written
            f.seek(reg["offset"] + region_patch.MARK_OFF)
            f.write(region_patch.make_marker(b"W", "ps", 1, reg["sha256"]))
            f.seek(reg["offset"] + HEADER + 500)
            f.write(b"\xEE" * 300)
        self.assertEqual(self.status()["state"], "pending")
        self.eng.sync_all("watch")
        self.assertEqual(self.data(), patched)
        self.eng.set_style(APPID, "original")
        self.assertEqual(self.data(), self.pristine)

    def test_waits_while_the_game_runs(self):
        pid = os.path.join(self.proc, "123")
        os.makedirs(pid)
        with open(os.path.join(pid, "cmdline"), "wb") as f:
            f.write(("reaper SteamLaunch AppId=%s -- %s/dx11/QuantumBreak.exe" % (
                APPID, self.install)).encode().replace(b" ", b"\0"))
        r = self.eng.set_style(APPID, "ps")
        self.assertTrue(r["ok"])
        self.assertEqual(self.data(), self.pristine)

    def test_backups_hold_only_the_regions(self):
        self.eng.set_style(APPID, "ps")
        bdir = os.path.join(self.eng.backup_root, APPID, "regions")
        total = sum(os.path.getsize(os.path.join(bdir, n)) for n in os.listdir(bdir))
        self.assertEqual(total, sum(r["size"] for r in self.regions))

    def test_revision_bump_redraws(self):
        self.eng.set_style(APPID, "ps")
        self.profile.revision = 2
        self.assertEqual(self.status()["state"], "pending")
        self.eng.sync_all("watch")
        self.assertEqual(self.status()["state"], "ok")


class QuantumBreakProfileTest(unittest.TestCase):
    def test_registered_and_consistent(self):
        p = PROFILES[APPID]
        self.assertEqual(p.file, "data/ep999-000.rmdp")
        self.assertEqual(len(p.regions), 8)
        for r in p.regions:
            self.assertEqual(r["size"], 128 + r["width"] * r["height"] * 4)
            self.assertEqual(len(r["sha256"]), 64)
            for c in r["cells"]:
                x0, y0, x1, y1 = c["clear"]
                self.assertTrue(0 <= x0 < x1 < r["width"] and 0 <= y0 < y1 < r["height"], r["key"])
        offsets = sorted((r["offset"], r["offset"] + r["size"]) for r in p.regions)
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(offsets, offsets[1:])), "regions overlap")

    def test_every_scale_has_the_same_cells(self):
        by_atlas = {}
        for r in PROFILES[APPID].regions:
            name = r["key"].split("/")[0]
            by_atlas.setdefault(name, []).append(sorted((c["kind"], c["role"]) for c in r["cells"]))
        for name, lists in by_atlas.items():
            self.assertTrue(all(l == lists[0] for l in lists), name)

    def test_masks_exist(self):
        mask_dir = os.path.join(ROOT, "py_modules", "button_glyphs", "masks")
        for n in ("cross", "circle", "square", "triangle", "L1", "R1", "L2", "R2"):
            self.assertTrue(os.path.exists(os.path.join(mask_dir, n + ".png")), n)


if __name__ == "__main__":
    unittest.main()
