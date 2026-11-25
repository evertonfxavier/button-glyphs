"""Serious Sam HD (First and Second Encounter): the two real profiles' tables and art, the HD builders (RGBA8 ovals, DXT5
prompts, the atlas written cell by cell, the pad), and a synthetic HD game through the real Engine with both vehicles.

Real game bytes are never in the repo: the real profiles are exercised with SYNTHETIC originals of the real sizes and
formats, the real bundled pictures, and the real table geometry (atlas cell rectangles).
"""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
import zlib
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("py_modules", "tests", "tools"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import croteam_fixture as F  # noqa: E402
import hd_fixture as hf  # noqa: E402
from button_glyphs import croteam_tex, engine, gro, image, overlay_patch  # noqa: E402
from button_glyphs.profiles import PROFILES, serious_hd, serious_sam_hd_tfe as tfe, serious_sam_hd_tse as tse  # noqa: E402
from button_glyphs.profiles import serious_tables  # noqa: E402
from button_glyphs.profiles.overlay_glyphs import (AtlasCells, PACKAGE_DIR, RoundSymbol, bleed, check_reencoded,  # noqa: E402
                                                    opaque_box, untouched_outside)
from test_backend import GLYPH_DIR, make_library  # noqa: E402
from test_overlay_patch import Cutter, rd, tree, wr  # noqa: E402
from verify_worlds import Crash  # noqa: E402

GAMES = {"41000": (tfe, serious_tables.GAMES["41000"]), "41010": (tse, serious_tables.GAMES["41010"])}


class TableTest(unittest.TestCase):
    def test_both_games_are_registered_with_the_conventions_of_the_overlay_strategy(self):
        for appid, (mod, table) in GAMES.items():
            p = PROFILES[appid]
            self.assertIs(p, mod.PROFILE)
            self.assertEqual((p.vehicle, p.archive, p.mount, p.loose_root, p.native_style, p.patch_styles, p.support),
                             ("gro", "zz_ButtonGlyphs.gro", table["mount"], ".", "xbox", ("ps",), "partial"))
            self.assertTrue(p.overlay_level)
            self.assertEqual(p.revision, 1)
            self.assertEqual(mod.VEHICLE, "gro")

    def test_the_entries_are_the_replace_rows_plus_the_pad_and_the_atlas(self):
        for appid, (mod, table) in GAMES.items():
            keys = {e["key"] for e in mod.PROFILE.entries}
            rows = [r for r in table["textures"] if r["action"] == "replace" and r["kind"] == "button"]
            self.assertEqual(keys, {serious_hd.key_of(r) for r in rows} | {"GamepadPerspective", "GlobalAtlas"})
            self.assertEqual(len(keys), len(mod.PROFILE.entries))
            for e in mod.PROFILE.entries:
                row = next(r for r in table["textures"] if serious_hd.key_of(r) == e["key"])
                self.assertEqual((e["path"], e["size"], e["sha256"], e["crc32"], e["gro"]),
                                 (row["path"], row["size"], row["sha256"], row["crc32"], row["gro"]))
                self.assertEqual(len(e["sha256"]), 64)

    def test_the_counts_the_pages_promise(self):
        self.assertEqual(len(tfe.PROFILE.entries), 10)          # 4 ovals + 4 discs + pad + atlas
        self.assertEqual(len(tse.PROFILE.entries), 20)          # 4 ovals + 12 prompts + 2 duplicates + pad + atlas
        cells = lambda p: p.builders["GlobalAtlas"].cells       # noqa: E731
        self.assertEqual(len(cells(tfe.PROFILE)), 4)
        self.assertEqual(len(cells(tse.PROFILE)), 14)           # 12 roles + the two baked duplicates

    def test_the_atlas_cells_are_exactly_the_replaced_rows_rects_and_never_a_thumbstick_cell(self):
        for appid, (mod, table) in GAMES.items():
            want = {tuple(r["atlas_part"]["rect"]) for r in table["textures"]
                    if r["action"] == "replace" and r.get("atlas_part")}
            got = {tuple(c[:4]) for c in mod.PROFILE.builders["GlobalAtlas"].cells}
            self.assertEqual(got, want)
            kept = {tuple(r["atlas_part"]["rect"]) for r in table["textures"]
                    if r["action"] in ("optional", "keep") and r.get("atlas_part")}
            self.assertFalse(got & kept, "a cell of a texture we keep would be redrawn")

    def test_rows_we_keep_are_not_in_the_profile(self):
        for appid, (mod, table) in GAMES.items():
            names = {e["path"] for e in mod.PROFILE.entries}
            for r in table["textures"]:
                if r["action"] in ("keep", "optional"):
                    self.assertNotIn(r["path"], names, r["id"])

    def test_the_duplicates_of_the_second_encounter_are_both_replaced(self):
        keys = {e["key"] for e in tse.PROFILE.entries}
        self.assertTrue({"XBoxButtonBack", "TSE_XBoxButtonBack", "XBoxTriggerButtonR", "TSE_XBoxTriggerButtonR"} <= keys)
        paths = {e["path"] for e in tse.PROFILE.entries}
        self.assertIn("Content/SeriousSamHD_TSE/Textures/Menu/XBoxButtonBack.tex", paths)
        self.assertIn("Content/SeriousSamHD/Textures/Menu/XBoxButtonBack.tex", paths)

    def test_our_archive_sorts_after_every_archive_of_the_supported_builds(self):
        for appid, (mod, table) in GAMES.items():
            names = [n for n, _, _ in table["load_order"]]
            self.assertGreater(mod.PROFILE.archive.lower(), max(n.lower() for n in names))
            self.assertGreater(mod.PROFILE.archive, max(names))

    def test_every_winning_archive_is_in_the_load_order(self):
        for appid, (mod, table) in GAMES.items():
            names = {n for n, _, _ in table["load_order"]}
            for e in mod.PROFILE.entries:
                self.assertIn(e["gro"], names)

    def test_the_vehicle_switch_is_one_argument_and_the_loose_paths_are_under_the_game_root(self):
        loose = serious_hd.make_profile("41010", vehicle="loose")
        rels = overlay_patch.active_rels(loose)
        self.assertEqual(len(rels), 20)
        self.assertTrue(all(r.startswith("Content/SeriousSamHD") and not r.startswith("Content/SeriousSamHD_TSE/Content")
                            for r in rels))
        self.assertEqual(overlay_patch.active_rels(loose, "gro"), ["Content/SeriousSamHD_TSE/zz_ButtonGlyphs.gro"])

    def test_the_boxes_match_the_page_numbers(self):
        spec = serious_hd.SPECS
        self.assertEqual(spec["41000"]["box"]["a"], (14, 14, 50, 50))
        self.assertEqual(spec["41010"]["box"]["a"], (7, 7, 56, 57))
        self.assertEqual(spec["41010"]["box"]["lb"], (4, 13, 59, 51))
        for s in spec.values():
            for box in list(s["box"].values()) + list(s["oval"].values()):
                self.assertTrue(0 <= box[0] < box[2] <= 64 and 0 <= box[1] < box[3] <= 64)


class BundledArtTest(unittest.TestCase):
    def test_every_role_picture_and_both_pads_exist_and_validate(self):
        for name in set(serious_hd.ROLE_ART.values()):
            rgba, w, h = image.read_png(os.path.join(PACKAGE_DIR, name + ".png"))
            self.assertEqual((w, h), (128, 128), name)
            self.assertIsNotNone(opaque_box(rgba, w, h), name)
        for appid, (mod, _) in GAMES.items():
            rgba, w, h = image.read_png(os.path.join(PACKAGE_DIR, serious_hd.SPECS[appid]["pad_art"] + ".png"))
            self.assertEqual((w, h), (512, 512))
            mod.PROFILE.preflight()

    def test_shoulders_and_triggers_use_the_solid_variants(self):
        for role in ("lb", "rb", "lt", "rt"):
            self.assertIn("plain-rectangle", serious_hd.ROLE_ART[role])
        self.assertEqual(serious_hd.ROLE_ART["l3"], "glyphs/ps/plain-L3")
        self.assertEqual(serious_hd.ROLE_ART["start"], "glyphs/ps/plain-big-option")
        self.assertEqual(serious_hd.ROLE_ART["a"], "glyphs/ps/outline-blue-cross")


def dxt_original(w=64, h=64, seed=1):
    stored, _ = F.flat_dxt(w, h, fmt=10, seed=seed)
    return F.build_hd(w, h, 10, stored)


def oval_original():
    return hf.oval_tex(3)


class RealProfilesOnSyntheticOriginalsTest(unittest.TestCase):
    """The real builders (real boxes, real art) on originals of the real format: where the symbol lands, how it reads."""

    @classmethod
    def setUpClass(cls):
        cls.tse = serious_hd.make_profile("41010", pad_picture=False)
        cls.tfe = serious_hd.make_profile("41000", pad_picture=False)

    def draw(self, profile, key):
        e = next(x for x in profile.entries if x["key"] == key)
        original = oval_original() if e["fmt"] == "rgba8" else dxt_original()
        e = dict(e, size=len(original))
        out = profile.builders[key].build(original, e, "ps", profile.pictures)
        return croteam_tex.decode(out)[0], profile.builders[key]

    def centre_ok(self, px, box, tol=1.6):
        vis = opaque_box(px, 32 if len(px) == 32 * 32 * 4 else 64, 32 if len(px) == 32 * 32 * 4 else 64, 24)
        x0, y0, x1, y1 = box
        self.assertAlmostEqual((vis[0] + vis[2]) / 2, (x0 + x1) / 2, delta=tol)
        self.assertAlmostEqual((vis[1] + vis[3]) / 2, (y0 + y1) / 2, delta=tol)
        return vis

    def test_round_symbols_on_the_discs_and_ovals_of_both_games(self):
        for profile, spec in ((self.tfe, serious_hd.SPECS["41000"]), (self.tse, serious_hd.SPECS["41010"])):
            for role, key in (("a", "XBoxButtonA"), ("b", "XBoxButtonB"), ("x", "XBoxButtonX"), ("y", "XBoxButtonY")):
                px, _ = self.draw(profile, key)
                box = spec["box"][role]
                vis = self.centre_ok(px, box)
                self.assertLessEqual(max(vis[2] - vis[0], vis[3] - vis[1]), min(box[2] - box[0], box[3] - box[1]) + 1, key)
            for role, key in (("a", "A_button"), ("b", "B_button"), ("x", "X_button"), ("y", "Y_button")):
                px, _ = self.draw(profile, key)
                vis = self.centre_ok(px, spec["oval"][role])
                self.assertLessEqual(vis[2] - vis[0], 20 + 1, key)       # the 20 x 24 oval's short side

    def test_the_second_encounters_shapes_are_fitted_into_their_boxes(self):
        spec = serious_hd.SPECS["41010"]["box"]
        for role, key in (("start", "XBoxButtonStart"), ("select", "XBoxButtonBack"), ("lb", "XBoxShoulderButtonL"),
                          ("rb", "XBoxShoulderButtonR"), ("lt", "XBoxTriggerButtonL"), ("rt", "XBoxTriggerButtonR"),
                          ("l3", "XBoxThumbstickTriggerL"), ("r3", "XBoxThumbstickTriggerR"),
                          ("select", "TSE_XBoxButtonBack"), ("rt", "TSE_XBoxTriggerButtonR")):
            px, _ = self.draw(self.tse, key)
            box = spec[role]
            vis = self.centre_ok(px, box, 2.0)
            self.assertLessEqual(vis[2] - vis[0], box[2] - box[0] + 1, key)
            self.assertLessEqual(vis[3] - vis[1], box[3] - box[1] + 1, key)
            self.assertTrue(vis[2] - vis[0] >= (box[2] - box[0]) - 2 or vis[3] - vis[1] >= (box[3] - box[1]) - 2, key)

    def test_the_colour_under_alpha_zero_is_the_arts_own_edge_colour(self):
        raw = self.tse.builders["XBoxButtonA"].intended({"width": 64, "height": 64, "key": "k"}, self.tse.pictures)
        visible = {tuple(raw[i:i + 3]) for i in range(0, len(raw), 4) if raw[i + 3] > 0}
        far = raw[:4]                                              # the corner: far from the symbol
        self.assertEqual(far[3], 0)
        self.assertIn(tuple(far[:3]), visible)                     # bled from the symbol, not black or white padding
        self.assertNotEqual(tuple(far[:3]), (0, 0, 0))

    def test_bleed_fills_only_transparent_pixels_with_the_nearest_visible_colour(self):
        px = bytes((10, 20, 30, 255)) + bytes(4) * 3
        out = bleed(px, 2, 2)
        self.assertEqual(out[:4], px[:4])
        for i in (1, 2, 3):
            self.assertEqual(tuple(out[i * 4:i * 4 + 4]), (10, 20, 30, 0))
        self.assertEqual(bleed(bytes(16), 2, 2), bytes(16))        # nothing visible: nothing to bleed

    def test_the_proof_refuses_a_wrong_decode_and_a_changed_head(self):
        original = dxt_original()
        good = croteam_tex.reencode(original, bytes((200, 10, 10, 255)) * 4096)
        check_reencoded(original, good, bytes((200, 10, 10, 255)) * 4096, "t", lossy=True)
        with self.assertRaises(RuntimeError):
            check_reencoded(original, good, bytes((10, 200, 10, 255)) * 4096, "t", lossy=True)
        with self.assertRaises(RuntimeError):
            check_reencoded(original, good.replace(b"Synthetic", b"Syntheticc", 1)[:len(good)], bytes(16384), "t", True)

    def test_the_atlas_is_written_cell_by_cell_and_nothing_else_moves(self):
        profile = serious_hd.make_profile("41010", pad_picture=False)
        first, _ = F.flat_dxt(512, 512, fmt=10, seed=2)
        second, _ = F.flat_dxt(1024, 512, fmt=10, seed=6)
        original, _ = F.build_atlas([(512, 512, 10, first), (1024, 512, 10, second)])
        e = dict(next(x for x in profile.entries if x["key"] == "GlobalAtlas"), size=len(original))
        out = profile.builders["GlobalAtlas"].build(original, e, "ps", profile.pictures)
        self.assertEqual(len(out), len(original))
        blocks = croteam_tex.find_blocks(original)
        b1, b2 = blocks[0], blocks[1]
        self.assertEqual(out[b2.start:b2.start + b2.size], original[b2.start:b2.start + b2.size])   # the second block
        self.assertEqual(out[:b1.start], original[:b1.start])
        changed = untouched_outside(original, out, blocks[0], [c[:4] for c in profile.builders["GlobalAtlas"].cells])
        self.assertGreater(changed, 14 * 200)                          # 14 cells of 256 level-0 blocks each really changed
        # a cell we do not redraw (a Thumbstick cell) is byte-for-byte the same in natural space
        keep = croteam_tex.region_pixels(original, b1, 320, 64, 64, 64)
        self.assertEqual(croteam_tex.region_pixels(out, b1, 320, 64, 64, 64), keep)
        # and the cell equals the standalone texture's drawing for the same role
        px, _ = self.draw(self.tse, "XBoxButtonA")
        cell = croteam_tex.region_pixels(out, b1, 192, 0, 64, 64)
        self.assertLess(sum(abs(a - b) for a, b in zip(px, cell)) / len(px), 3.0)

    def test_an_atlas_that_is_not_the_pinned_one_is_refused(self):
        profile = serious_hd.make_profile("41010", pad_picture=False)
        first, _ = F.flat_dxt(256, 128, fmt=10, seed=2)                 # TFE-sized first block in a TSE profile
        original, _ = F.build_atlas([(256, 128, 10, first)])
        e = dict(next(x for x in profile.entries if x["key"] == "GlobalAtlas"), size=len(original))
        with self.assertRaises(RuntimeError):
            profile.builders["GlobalAtlas"].build(original, e, "ps", profile.pictures)

    def test_an_atlas_with_too_few_blocks_for_the_cells_block_index_is_refused(self):
        first, _ = F.flat_dxt(128, 64, fmt=10, seed=2)
        original, _ = F.build_atlas([(128, 64, 10, first)])
        symbol = RoundSymbol("glyphs/ps/a-icon", hf.DISC_BOX, "bleed")
        art = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, art, True)
        hf.make_art(art)
        from button_glyphs.profiles.region_glyphs import PictureLoader
        with self.assertRaises(RuntimeError):
            AtlasCells(3, [(64, 0, 64, 64, symbol)]).build(
                original, {"key": "GlobalAtlas", "width": 128, "height": 64, "fmt": "dxt5"}, "ps", PictureLoader(art))

    def test_a_builder_that_writes_outside_its_cells_is_refused_by_the_build_itself(self):
        first, _ = F.flat_dxt(128, 64, fmt=10, seed=2)
        original, _ = F.build_atlas([(128, 64, 10, first)])
        symbol = RoundSymbol("glyphs/ps/a-icon", hf.DISC_BOX, "bleed")
        art = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, art, True)
        hf.make_art(art)
        from button_glyphs.profiles.region_glyphs import PictureLoader
        real = croteam_tex.with_region

        def sloppy(data, block, x, y, rgba, w, h):
            out = real(data, block, x, y, rgba, w, h)
            return real(out, block, 0, 0, bytes((9, 9, 9, 255)) * 4096, 64, 64)       # also redraws a cell nobody asked for

        with mock.patch.object(croteam_tex, "with_region", sloppy), self.assertRaises(RuntimeError):
            AtlasCells(0, [(64, 0, 64, 64, symbol)]).build(
                original, {"key": "GlobalAtlas", "width": 128, "height": 64, "fmt": "dxt5"}, "ps", PictureLoader(art))

    def test_a_write_outside_the_cells_is_caught_by_the_proof(self):
        first, _ = F.flat_dxt(128, 64, fmt=10, seed=2)
        original, _ = F.build_atlas([(128, 64, 10, first)])
        blk = croteam_tex.find_blocks(original)[0]
        stray = croteam_tex.with_region(original, blk, 64, 0, bytes((9, 9, 9, 255)) * 4096, 64, 64)
        with self.assertRaises(RuntimeError):
            untouched_outside(original, stray, blk, [(0, 0, 64, 64)])

    def test_the_pad_is_the_owners_picture_through_a_dxt_round_trip(self):
        profile = serious_hd.make_profile("41000", pad_picture=True)
        e = next(x for x in profile.entries if x["key"] == "GamepadPerspective")
        stored, _ = F.flat_dxt(512, 512, fmt=10, seed=5)
        original = F.build_hd(512, 512, 10, stored)
        out = profile.builders["GamepadPerspective"].build(original, dict(e, size=len(original)), "ps", profile.pictures)
        self.assertEqual(len(out), len(original))
        self.assertNotEqual(out, original)


class HdCase(unittest.TestCase):
    vehicle = "gro"

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        common = make_library(self.home, [(hf.APPID, "Serious Sam HD", hf.INSTALLDIR, "4")])
        self.install = os.path.join(common, hf.INSTALLDIR)
        self.art = os.path.join(self.home, "art")
        hf.build_game(self.install)
        hf.make_art(self.art)
        self.entries = hf.entries_for(self.install)
        self.profile = hf.make_profile(self.entries, self.art, vehicle=self.vehicle)
        self.pristine = tree(self.install)
        self.proc = os.path.join(self.home, "proc")
        os.makedirs(self.proc)
        self.eng = engine.Engine(home=self.home, settings_dir=os.path.join(self.home, "settings"),
                                 runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
                                 profiles={hf.APPID: self.profile}, proc_root=self.proc)

    def set(self, style):
        return self.eng.set_style(hf.APPID, style)

    def ok(self, style):
        res = self.set(style)
        self.assertTrue(res["ok"], res)
        return res

    def status(self):
        return next(g for g in self.eng.list_games() if g["appid"] == hf.APPID)

    def added(self):
        return {k: v for k, v in tree(self.install).items() if k not in self.pristine and v != "DIR"}

    def clean_world(self):
        shutil.rmtree(self.home, ignore_errors=True)
        self.setUp()


class HdEngineTest(HdCase):
    OUR = "Content/SeriousSamHD_X/zz_ButtonGlyphs.gro"

    def test_ps_adds_one_archive_inside_the_mount_folder_and_original_removes_it(self):
        self.ok("ps")
        self.assertEqual(list(self.added()), [self.OUR])
        for k, v in self.pristine.items():
            self.assertEqual(tree(self.install).get(k), v, k)
        st = self.status()
        self.assertEqual((st["state"], st["applied"], st["code"]), ("ok", "ps", "active"))
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)

    def test_the_archive_holds_every_entry_and_the_atlas_differs_only_in_its_first_block(self):
        self.ok("ps")
        path = os.path.join(self.install, *self.OUR.split("/"))
        with zipfile.ZipFile(path) as z:
            self.assertEqual(z.testzip(), None)
            self.assertEqual(sorted(z.namelist()), sorted(e["path"] for e in self.entries))
            new_atlas = z.read(hf.ATLAS)
        old = hf.atlas()
        self.assertEqual(len(new_atlas), len(old))
        b_old, b_new = croteam_tex.find_blocks(old), croteam_tex.find_blocks(new_atlas)
        self.assertNotEqual(new_atlas[b_new[0].start:b_new[0].start + b_new[0].size],
                            old[b_old[0].start:b_old[0].start + b_old[0].size])
        self.assertEqual(new_atlas[b_new[1].start:], old[b_old[1].start:])      # the second block and the tail

    def test_a_cell_equals_the_standalone_drawing(self):
        self.ok("ps")
        path = os.path.join(self.install, *self.OUR.split("/"))
        with zipfile.ZipFile(path) as z:
            atlas, disc = z.read(hf.ATLAS), z.read(hf.DISC)
        blk = croteam_tex.find_blocks(atlas)[0]
        cell = croteam_tex.region_pixels(atlas, blk, 64, 0, 64, 64)
        alone = croteam_tex.decode(disc)[0]
        self.assertLess(sum(abs(a - b) for a, b in zip(cell, alone)) / len(alone), 3.0)

    def test_status_after_apply_is_answered_without_building_or_inflating(self):
        self.ok("ps")
        boom = mock.Mock(side_effect=AssertionError("built"))
        with mock.patch.object(overlay_patch, "read_originals", boom), mock.patch.object(zipfile.ZipFile, "read", boom):
            self.assertEqual(self.status()["code"], "active")
            self.assertEqual([r["changed"] for r in self.eng.sync_all("watch")], [False])

    def test_an_original_entry_that_changed_is_an_unknown_build(self):
        hf.build_game(self.install, variant=1)
        self.pristine = tree(self.install)
        self.assertFalse(self.set("ps")["ok"])
        self.assertEqual(tree(self.install), self.pristine)
        self.assertEqual(self.status()["state"], "error")

    def test_cut_at_every_event_of_apply_and_original(self):
        with Cutter([self.install, self.eng.backup_root]) as inj:
            self.ok("ps")
        n = inj.count
        for k in range(n):
            self.clean_world()
            try:
                with Cutter([self.install, self.eng.backup_root], k=k):
                    self.set("ps")
            except Crash:
                pass
            for name, v in self.pristine.items():
                self.assertEqual(tree(self.install).get(name), v, k)
            self.ok("original")
            self.assertEqual(tree(self.install), self.pristine, k)


class HdLooseVehicleTest(HdCase):
    vehicle = "loose"

    def test_the_entries_become_loose_files_under_the_game_root_not_under_the_mount(self):
        self.ok("ps")
        self.assertEqual(sorted(self.added()), sorted(e["path"] for e in self.entries))
        self.assertFalse([k for k in self.added() if k.startswith("Content/SeriousSamHD_X")])
        atlas = rd(os.path.join(self.install, *hf.ATLAS.split("/")))
        self.assertEqual(len(atlas), len(hf.atlas()))
        self.assertEqual(self.status()["state"], "ok")
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)              # files and the folders we made

    def test_switching_the_vehicle_in_a_later_release_cleans_the_old_one(self):
        self.ok("ps")
        gro_profile = hf.make_profile(self.entries, self.art, vehicle="gro")
        eng = engine.Engine(home=self.home, settings_dir=os.path.join(self.home, "settings"),
                            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
                            profiles={hf.APPID: gro_profile}, proc_root=self.proc)
        self.assertEqual(self.status()["state"], "ok")
        res = eng.set_style(hf.APPID, "ps")
        self.assertTrue(res["ok"], res)
        self.assertEqual(list(self.added()), ["Content/SeriousSamHD_X/zz_ButtonGlyphs.gro"])
        eng.set_style(hf.APPID, "original")
        self.assertEqual(tree(self.install), self.pristine)

    def test_a_cut_at_every_event_recovers(self):
        with Cutter([self.install, self.eng.backup_root]) as inj:
            self.ok("ps")
        for k in range(inj.count):
            self.clean_world()
            try:
                with Cutter([self.install, self.eng.backup_root], k=k):
                    self.set("ps")
            except Crash:
                pass
            self.ok("original")
            self.assertEqual(tree(self.install), self.pristine, k)


if __name__ == "__main__":
    unittest.main()
