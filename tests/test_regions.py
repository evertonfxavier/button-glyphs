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
from button_glyphs import image  # noqa: E402
from button_glyphs.profiles.region_glyphs import (  # noqa: E402
    HEADER, MaskLoader, PictureLoader, RegionGlyphProfile)
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

    # ---- refusal is visible, and nothing is written when an asset is unusable

    def test_refusal_shows_the_error_not_pending(self):
        reg = self.regions[0]
        with open(self.path, "r+b") as f:
            f.seek(reg["offset"] + HEADER + 100)
            f.write(b"\xAB\xCD")                    # a different build of the texture
        before = self.data()
        self.assertFalse(self.eng.set_style(APPID, "ps")["ok"])
        self.assertEqual(self.data(), before)
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertIn("not supported", st["message"])
        self.assertNotIn("will be applied", st["message"])

    def test_missing_mask_fails_before_the_first_write(self):
        self.profile.masks = MaskLoader(os.path.join(self.home, "no-masks"))
        r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("mask", r["errors"][0])
        self.assertEqual(self.data(), self.pristine)             # not one byte, no marker
        self.assertFalse(os.path.exists(os.path.join(self.eng.backup_root, APPID, "regions")))
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertIn("mask", st["message"])

    def test_truncated_mask_fails_before_the_first_write(self):
        mask_dir = os.path.join(self.home, "masks")
        os.makedirs(mask_dir)
        src = os.path.join(ROOT, "py_modules", "button_glyphs", "masks")
        for n in os.listdir(src):
            if os.path.isfile(os.path.join(src, n)):             # masks/sfh/ etc. are folders of per-game art
                shutil.copy(os.path.join(src, n), mask_dir)
        with open(os.path.join(mask_dir, "cross.png"), "r+b") as f:
            f.truncate(40)
        self.profile.masks = MaskLoader(mask_dir)
        r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertEqual(self.data(), self.pristine)
        self.assertEqual(self.status()["state"], "error")

    def test_original_still_restores_when_assets_are_gone(self):
        self.eng.set_style(APPID, "ps")
        self.profile.masks = MaskLoader(os.path.join(self.home, "no-masks"))
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(self.data(), self.pristine)

    def test_wrong_rendered_length_is_refused_before_writing(self):
        class ShortProfile:
            file, regions, revision, patch_styles = ARCHIVE, self.regions, 1, ("ps",)

            @staticmethod
            def render(original, region, style):
                return original[:-4]

        bdir = os.path.join(self.home, "bk")
        rows = region_patch.plan(self.profile, self.install, "ps")
        region_patch.ensure_backups(self.profile, self.install, bdir, rows)
        with self.assertRaises(RuntimeError):
            region_patch.apply(ShortProfile, self.install, bdir, rows, "ps")
        self.assertEqual(self.data(), self.pristine)


class QuantumBreakProfileTest(unittest.TestCase):
    def test_registered_and_consistent(self):
        p = PROFILES[APPID]
        self.assertEqual(p.file, "data/ep999-000.rmdp")
        self.assertEqual(len(p.regions), 9)       # 8 prompt atlases + the controller picture
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


# --------------------------------------------------------------------------- whole-picture regions

PW = PH = 64                       # synthetic "controller.tex"
CLEAR = (130, 125, 100)            # colour the game keeps under transparent pixels (B, G, R)
ART_W, ART_H = 20, 10
BOX = (10, 14, 39, 49)             # 30 x 36, inclusive; the art (20 x 10) is centred in it


def picture_texture():
    """An old "pad drawing": opaque white blob on a transparent background with the game's bleed."""
    px = bytearray((CLEAR + (0,)) * (PW * PH))
    for y in range(20, 44):
        for x in range(12, 52):
            px[(y * PW + x) * 4:(y * PW + x) * 4 + 4] = bytes((255, 255, 255, 255))
    px[(30 * PW + 30) * 4 + 3] = 90                       # a soft pixel
    head = bytearray(128)
    head[:4] = b"DDS "
    head[12:16] = PH.to_bytes(4, "little")
    head[16:20] = PW.to_bytes(4, "little")
    return bytes(head) + bytes(px)


def art_rgba():
    """RGBA art: opaque red frame, a half-transparent blue pixel, transparent elsewhere."""
    px = bytearray(ART_W * ART_H * 4)
    for y in range(ART_H):
        for x in range(ART_W):
            if x in (0, ART_W - 1) or y in (0, ART_H - 1):
                px[(y * ART_W + x) * 4:(y * ART_W + x) * 4 + 4] = bytes((200, 20, 30, 255))
    px[(5 * ART_W + 7) * 4:(5 * ART_W + 7) * 4 + 4] = bytes((10, 40, 220, 100))
    return bytes(px)


class PictureRegionTest(unittest.TestCase):
    """A region replaced as a whole (QB controller.tex): render, refusal, exact undo."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.art_dir = os.path.join(self.home, "art")
        os.makedirs(self.art_dir)
        image.write_png(os.path.join(self.art_dir, "pad.png"), art_rgba(), ART_W, ART_H)
        common = make_library(self.home, [(APPID, "Quantum Break", "QuantumBreak", "4")])
        self.install = os.path.join(common, "QuantumBreak")
        os.makedirs(os.path.join(self.install, "data"))
        self.tex = picture_texture()
        self.region = {
            "key": "controller/menu", "offset": 3000, "size": len(self.tex), "width": PW,
            "height": PH, "sha256": hashlib.sha256(self.tex).hexdigest(), "cells": [],
            "replace": {"image": "pad", "box": BOX, "clear": CLEAR}}
        self.profile = RegionGlyphProfile(
            appid=APPID, name="Quantum Break", file=ARCHIVE, regions=[self.region],
            roles=[("a", "A / X")], native_style="xbox", revision=1)
        self.profile.pictures = PictureLoader(self.art_dir)
        self.path = os.path.join(self.install, ARCHIVE)
        blob = bytearray(os.urandom(3000 + len(self.tex) + 5000))
        blob[3000:3000 + len(self.tex)] = self.tex
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

    def body(self, raw):
        return raw[3000 + HEADER:3000 + len(self.tex)]

    def px(self, body, x, y):
        return tuple(body[(y * PW + x) * 4:(y * PW + x) * 4 + 4])

    def test_render_is_deterministic_and_keeps_size_and_header(self):
        a = self.profile.render(self.tex, self.region, "ps")
        self.assertEqual(a, self.profile.render(self.tex, self.region, "ps"))
        self.assertEqual(len(a), len(self.tex))
        self.assertEqual(a[:HEADER], self.tex[:HEADER])
        self.assertNotEqual(a, self.tex)

    def test_old_picture_is_wiped_and_the_art_is_centred_in_the_box(self):
        body = self.profile.render(self.tex, self.region, "ps")[HEADER:]
        # outside the art everything is transparent with the game's bleed colour,
        # including the old white blob and its soft pixel
        ox = BOX[0] + (BOX[2] - BOX[0] + 1 - ART_W) // 2
        oy = BOX[1] + (BOX[3] - BOX[1] + 1 - ART_H) // 2
        self.assertEqual((ox, oy), (15, 27))
        for y in range(PH):
            for x in range(PW):
                p = self.px(body, x, y)
                inside_frame = (x in (ox, ox + ART_W - 1) and oy <= y < oy + ART_H) or (
                    y in (oy, oy + ART_H - 1) and ox <= x < ox + ART_W)
                if not inside_frame and (x, y) != (ox + 7, oy + 5):
                    self.assertEqual(p, CLEAR + (0,), (x, y))
        # straight alpha, channel order B, G, R, A
        self.assertEqual(self.px(body, ox, oy), (30, 20, 200, 255))
        self.assertEqual(self.px(body, ox + 7, oy + 5), (220, 40, 10, 100))

    def test_apply_marks_the_header_only_and_undo_is_byte_exact(self):
        r = self.eng.set_style(APPID, "ps")
        self.assertTrue(r["ok"], r)
        now = self.data()
        self.assertEqual(len(now), len(self.pristine))
        hdr, orig = now[3000:3000 + HEADER], self.tex[:HEADER]
        self.assertTrue(hdr[0x20:0x4C].startswith(region_patch.MAGIC))
        self.assertEqual(hdr[:0x20], orig[:0x20])
        self.assertEqual(hdr[0x4C:], orig[0x4C:])
        self.assertEqual(now[:3000], self.pristine[:3000])                 # nothing else moved
        self.assertEqual(now[3000 + len(self.tex):], self.pristine[3000 + len(self.tex):])
        self.assertNotEqual(self.body(now), self.body(self.pristine))
        st = next(g for g in self.eng.list_games() if g["appid"] == APPID)
        self.assertEqual((st["state"], st["applied"]), ("ok", "ps"))
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(hashlib.sha256(self.data()).hexdigest(),
                         hashlib.sha256(self.pristine).hexdigest())
        # and the cycle ps -> xbox(original) -> ps -> original stays exact
        self.eng.set_style(APPID, "ps")
        self.eng.set_style(APPID, "xbox")
        self.assertEqual(self.data(), self.pristine)

    def test_reapply_after_steam_gives_the_same_bytes(self):
        self.eng.set_style(APPID, "ps")
        patched = self.data()
        with open(self.path, "wb") as f:
            f.write(self.pristine)
        self.eng.sync_all("watch")
        self.assertEqual(self.data(), patched)

    def test_different_texture_is_refused_and_left_alone(self):
        with open(self.path, "r+b") as f:
            f.seek(3000 + HEADER + 40 * 4)
            f.write(b"\x01\x02")                      # another build of controller.tex
        before = self.data()
        r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("not supported", r["errors"][0])
        self.assertEqual(self.data(), before)

    def assert_untouched_and_error(self, needle):
        r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn(needle, r["errors"][0])
        self.assertEqual(hashlib.sha256(self.data()).hexdigest(),
                         hashlib.sha256(self.pristine).hexdigest())
        self.assertNotIn(region_patch.MAGIC, self.data())          # no marker anywhere
        self.assertFalse(os.path.exists(os.path.join(self.eng.backup_root, APPID, "regions")))
        st = next(g for g in self.eng.list_games() if g["appid"] == APPID)
        self.assertEqual(st["state"], "error")
        self.assertIn(needle, st["message"])

    def test_missing_picture_writes_nothing(self):
        os.remove(os.path.join(self.art_dir, "pad.png"))
        self.assert_untouched_and_error("pad")

    def test_truncated_picture_writes_nothing(self):
        path = os.path.join(self.art_dir, "pad.png")
        with open(path, "rb") as f:
            data = f.read()
        with open(path, "wb") as f:
            f.write(data[:len(data) // 2])
        self.assert_untouched_and_error("pad")

    def test_picture_larger_than_its_box_writes_nothing(self):
        self.region["replace"]["box"] = (10, 14, 20, 20)          # 11 x 7, art is 20 x 10
        self.assert_untouched_and_error("does not fit")

    def test_box_outside_the_texture_writes_nothing(self):
        self.region["replace"]["box"] = (10, 14, 39, 64)
        self.assert_untouched_and_error("outside the texture")

    def test_a_bad_picture_stops_the_apply_before_earlier_regions_are_written(self):
        """Region 0 is fine, region 1 has a missing picture: region 0 must stay original."""
        tex0 = texture(5)
        second = dict(self.region, offset=3000 + len(self.tex) + 1000,
                      replace=dict(self.region["replace"], image="missing"))
        first = {"key": "prompts/t0", "offset": 3000, "size": len(tex0), "width": W,
                 "height": H, "sha256": hashlib.sha256(tex0).hexdigest(), "cells": cells()}
        second["offset"] = 3000 + len(tex0) + 1000
        blob = bytearray(os.urandom(second["offset"] + len(self.tex) + 3000))
        blob[3000:3000 + len(tex0)] = tex0
        blob[second["offset"]:second["offset"] + len(self.tex)] = self.tex
        with open(self.path, "wb") as f:
            f.write(blob)
        self.pristine = bytes(blob)
        self.eng.profiles[APPID].regions = [first, second]
        self.assert_untouched_and_error("missing")

    def test_render_refuses_wrong_size_or_a_box_outside_the_texture(self):
        with self.assertRaises(ValueError):
            self.profile.render(self.tex[:-4], self.region, "ps")
        bad = dict(self.region, replace=dict(self.region["replace"], box=(10, 14, 39, 64)))
        with self.assertRaises(ValueError):
            self.profile.render(self.tex, bad, "ps")
        small = dict(self.region, width=32, height=32)          # header says 64x64 worth of pixels
        with self.assertRaises(ValueError):
            self.profile.render(self.tex, small, "ps")

    def test_interrupted_write_is_recovered(self):
        self.eng.set_style(APPID, "ps")
        patched = self.data()
        with open(self.path, "r+b") as f:
            f.seek(3000 + region_patch.MARK_OFF)
            f.write(region_patch.make_marker(b"W", "ps", 1, self.region["sha256"]))
            f.seek(3000 + HEADER + 500)
            f.write(b"\xEE" * 300)
        self.eng.sync_all("watch")
        self.assertEqual(self.data(), patched)
        self.eng.set_style(APPID, "original")
        self.assertEqual(self.data(), self.pristine)


class QuantumBreakPictureTest(unittest.TestCase):
    """The real controller entry: bundled DualSense art placed on a blank 1024 texture."""

    def region(self):
        return next(r for r in PROFILES[APPID].regions if r["key"] == "controller/menu")

    def test_entry_is_consistent(self):
        r = self.region()
        self.assertEqual((r["width"], r["height"], r["size"]), (1024, 1024, 128 + 1024 * 1024 * 4))
        self.assertEqual(len(r["sha256"]), 64)
        self.assertEqual(r["cells"], [])
        x0, y0, x1, y1 = r["replace"]["box"]
        self.assertTrue(0 <= x0 < x1 < 1024 and 0 <= y0 < y1 < 1024)
        self.assertEqual(len(r["replace"]["clear"]), 3)
        self.assertEqual(PROFILES[APPID].revision, 2)

    def test_bundled_art_fits_its_box_and_renders_centred(self):
        r = self.region()
        prof = PROFILES[APPID]
        rgba, w, h = image.read_png(os.path.join(ROOT, "py_modules", "button_glyphs", "masks",
                                                 r["replace"]["image"] + ".png"))
        x0, y0, x1, y1 = r["replace"]["box"]
        self.assertLessEqual(w, x1 - x0 + 1)
        self.assertLessEqual(h, y1 - y0 + 1)
        self.assertTrue(630 <= w <= 720, w)                      # visual weight of the Xbox pad
        blank = bytes(128) + bytes(1024 * 1024 * 4)
        out = prof.render(blank, r, "ps")
        self.assertEqual(out[:128], blank[:128])
        self.assertEqual(out, prof.render(blank, r, "ps"))
        a = out[128 + 3::4]
        xs = [x for x in range(1024) if any(a[y * 1024 + x] > 8 for y in range(y0, y1 + 1))]
        ys = [y for y in range(y0, y1 + 1) if any(a[y * 1024 + x] > 8 for x in range(x0, x1 + 1))]
        self.assertEqual((xs[0] + xs[-1]) / 2, 511.5)
        self.assertEqual((ys[0] + ys[-1]) / 2, 511.5)
        self.assertEqual(xs[-1] - xs[0] + 1, w)
        # the cut-out has a transparent corner and an opaque body, no halo of near-white pixels
        self.assertEqual(a[x0 + 1024 * y0], 0)
        self.assertGreater(sum(1 for v in a if v == 255), 100000)


if __name__ == "__main__":
    unittest.main()
