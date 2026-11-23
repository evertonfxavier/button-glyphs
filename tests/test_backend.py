import json
import os
import re
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from button_glyphs import engine, image, steam  # noqa: E402
from button_glyphs.forge import (  # noqa: E402
    DataFile, Forge, ForgeError, append_entry_payload, lzo1x_decompress, lzo1x_literal,
)
from button_glyphs.profiles import PROFILES  # noqa: E402
import forge_fixture as fx  # noqa: E402

GLYPH_DIR = os.path.join(ROOT, "py_modules", "button_glyphs", "glyphs")


def texture_rgba(path):
    df = Forge(path).load("Game Bootstrap Settings")
    res = df.read_resource(fx.TEX_ID)
    w, h, fmt, off, size = fx.test_profile()._layout(res)
    return image.decode(res[off:off + size], w, h, "DXT3")


def box_pixels(rgba, box, w=64):
    x0, y0, x1, y1 = box
    return [tuple(rgba[(y * w + x) * 4:(y * w + x) * 4 + 4])
            for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


class LzoTest(unittest.TestCase):
    def test_literal_stream_roundtrip(self):
        for n in (0, 1, 3, 4, 200, 238, 239, 300, 32768):
            data = bytes((i * 31) & 255 for i in range(n))
            self.assertEqual(lzo1x_decompress(lzo1x_literal(data), n), data)

    def test_back_reference(self):
        # 4 literals "abcd", then an M2 match: length 3, distance 4, then end marker
        stream = bytes([17 + 4]) + b"abcd" + bytes([(2 << 5) | (3 << 2), 0]) + b"\x11\x00\x00"
        self.assertEqual(lzo1x_decompress(stream), b"abcdabc")

    def test_size_mismatch_raises(self):
        with self.assertRaises(ForgeError):
            lzo1x_decompress(lzo1x_literal(b"abc"), 4)


class ForgeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "DataPC.forge")
        fx.build_forge(self.path)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_index_and_roundtrip(self):
        fg = Forge(self.path)
        self.assertEqual([e.name for e in fg.entries],
                         ["GlobalMetaFile", "Game Bootstrap Settings", "Game Fix"])
        entry = fg.find("Game Bootstrap Settings")
        payload = fg.read_payload(entry)
        df = DataFile(payload)
        self.assertEqual(df.tobytes(), payload)
        self.assertEqual(set(df.resources), {fx.TEX_ID, fx.OTHER_ID})

    def test_append_payload_keeps_other_entries(self):
        before = Forge(self.path)
        entry = before.find("Game Bootstrap Settings")
        df = before.load("Game Bootstrap Settings")
        start, size = df.resources[fx.OTHER_ID]
        df.patch_resource(fx.OTHER_ID, 100, b"PATCHED!")
        append_entry_payload(self.path, entry, df.tobytes())

        after = Forge(self.path)
        moved = after.find("Game Bootstrap Settings")
        self.assertGreater(moved.offset, entry.offset)
        for a, b in zip(before.entries, after.entries):
            if a.name != moved.name:
                self.assertEqual((a.offset, a.size), (b.offset, b.size))
        df2 = after.load("Game Bootstrap Settings")
        self.assertEqual(df2.read_resource(fx.OTHER_ID)[100:108], b"PATCHED!")
        self.assertEqual(df2.data_block.bad_checksums(), 0)
        self.assertEqual(df2.table_block.bad_checksums(), 0)


class ImageTest(unittest.TestCase):
    def test_dxt3_solid_roundtrip(self):
        px = [(200, 40, 60, 255)] * 8 + [(0, 0, 0, 0)] * 8
        rgba = b"".join(bytes(p) for p in px)
        enc = image.encode_block(image.block_pixels(rgba, 4, 0, 0), "DXT3")
        dec = image.decode(enc, 4, 4, "DXT3")
        self.assertEqual(dec[3], 255)
        self.assertEqual(dec[8 * 4 + 3], 0)
        self.assertTrue(abs(dec[0] - 200) < 10 and abs(dec[1] - 40) < 10)

    def test_fit_icon_keeps_aspect_and_alpha(self):
        rgba, w, h = image.read_png(os.path.join(GLYPH_DIR, "ps", "plain-cross.png"))
        small, sw, sh = image.fit_icon(rgba, w, h, 22, 23)
        self.assertEqual((sw, sh), (22, 22))
        self.assertEqual(small[3], 0)  # corner stays transparent
        centre = (11 * 22 + 11) * 4
        self.assertGreater(small[centre + 3], 200)


class SteamTest(unittest.TestCase):
    def test_parse_vdf_and_tools_filtered(self):
        home = tempfile.mkdtemp()
        try:
            make_library(home, [("15100", "Assassin's Creed", "Assassins Creed", "4"),
                                ("1493710", "Proton Experimental", "Proton - Experimental", "4"),
                                ("228980", "Steamworks Common Redistributables", "Steamworks Shared", "4")])
            apps = steam.installed_apps(home)
            self.assertEqual(list(apps), ["15100"])
            self.assertEqual(apps["15100"]["buildid"], "100")
            self.assertTrue(steam.is_ready(apps["15100"]))
        finally:
            shutil.rmtree(home)

    def test_running_detection(self):
        app = {"install_path": "/home/deck/.local/share/Steam/steamapps/common/Assassins Creed"}
        cmd = ["reaper SteamLaunch AppId=15100 -- /home/deck/.local/share/Steam/steamapps/"
               "common/Assassins Creed/AssassinsCreed_Dx9.exe"]
        self.assertTrue(steam.is_running(app, cmd))
        self.assertFalse(steam.is_running(app, ["/usr/bin/steam -gamepadui"]))


def make_library(home, apps, buildid="100"):
    root = os.path.join(home, ".local/share/Steam")
    steamapps = os.path.join(root, "steamapps")
    os.makedirs(steamapps, exist_ok=True)
    with open(os.path.join(steamapps, "libraryfolders.vdf"), "w") as f:
        f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % root)
    for appid, name, installdir, flags in apps:
        os.makedirs(os.path.join(steamapps, "common", installdir), exist_ok=True)
        with open(os.path.join(steamapps, "appmanifest_%s.acf" % appid), "w") as f:
            f.write('"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"%s"\n\t"StateFlags"\t\t"%s"\n'
                    '\t"installdir"\t\t"%s"\n\t"buildid"\t\t"%s"\n}\n'
                    % (appid, name, flags, installdir, buildid))
    return os.path.join(steamapps, "common")


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        common = make_library(self.home, [("15100", "Assassin's Creed", "Assassins Creed", "4")])
        self.game = os.path.join(common, "Assassins Creed", "DataPC.forge")
        fx.build_forge(self.game)
        self.original = engine.sha256_file(self.game)
        self.proc = os.path.join(self.home, "proc")
        os.makedirs(self.proc)
        self.eng = engine.Engine(
            home=self.home,
            settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"),
            glyph_dir=GLYPH_DIR,
            profiles={"15100": fx.test_profile()},
            proc_root=self.proc,
        )

    def tearDown(self):
        shutil.rmtree(self.home)

    def game_status(self):
        return next(g for g in self.eng.list_games() if g["appid"] == "15100")

    def test_apply_switch_and_restore(self):
        result = self.eng.set_style("15100", "ps")
        self.assertTrue(result["ok"], result)
        trailer = engine.read_trailer(self.game)
        self.assertEqual(trailer["style"], "ps")
        self.assertEqual(trailer["source_sha256"], self.original)
        backup = self.eng.backup_path("15100", fx.test_profile())
        self.assertEqual(engine.sha256_file(backup), self.original)

        rgba = texture_rgba(self.game)
        # the yellow head square became the teal PS triangle: no more yellow, some teal
        head = box_pixels(rgba, fx.BOXES["head"])
        self.assertFalse(any(r > 200 and g > 180 and b < 60 and a > 200 for r, g, b, a in head))
        self.assertTrue(any(g > 150 and b > 140 and r < 80 and a > 100 for r, g, b, a in head))
        # pixels away from the boxes are untouched (transparent)
        self.assertEqual(rgba[(60 * 64 + 2) * 4 + 3], 0)
        self.assertEqual(self.game_status()["state"], "ok")

        self.assertTrue(self.eng.set_style("15100", "xbox")["ok"])
        self.assertEqual(engine.read_trailer(self.game)["style"], "xbox")
        self.assertEqual(engine.read_trailer(self.game)["source_sha256"], self.original)

        self.assertTrue(self.eng.set_style("15100", "original")["ok"])
        self.assertIsNone(engine.read_trailer(self.game))
        self.assertEqual(engine.sha256_file(self.game), self.original)

    def test_reapply_after_game_update(self):
        self.eng.set_style("15100", "ps")
        # Steam updates the game: new original file and new buildid
        fx.build_forge(self.game, variant=1)
        new_original = engine.sha256_file(self.game)
        make_library(self.home, [("15100", "Assassin's Creed", "Assassins Creed", "4")], buildid="200")
        self.assertEqual(self.game_status()["state"], "pending")

        results = self.eng.sync_all("watch")
        self.assertTrue(results[0]["changed"], results)
        trailer = engine.read_trailer(self.game)
        self.assertEqual(trailer["style"], "ps")
        self.assertEqual(trailer["source_sha256"], new_original)
        self.assertEqual(trailer["buildid"], "200")
        cfg = self.eng.load_config()["games"]["15100"]
        self.assertIn("build 100 -> 200", cfg["last_event"])
        backup = self.eng.backup_path("15100", fx.test_profile())
        self.assertEqual(engine.sha256_file(backup), new_original)
        # a second pass is a no-op
        self.assertFalse(self.eng.sync_all("watch")[0]["changed"])

    def test_waits_while_running_or_updating(self):
        os.makedirs(os.path.join(self.proc, "4242"))
        with open(os.path.join(self.proc, "4242", "cmdline"), "wb") as f:
            f.write(os.path.dirname(self.game).encode() + b"/AssassinsCreed_Dx9.exe\0")
        result = self.eng.set_style("15100", "ps")
        self.assertTrue(result["ok"])
        self.assertFalse(result["changed"])
        self.assertIsNone(engine.read_trailer(self.game))
        self.assertEqual(self.game_status()["state"], "running")

        shutil.rmtree(os.path.join(self.proc, "4242"))
        make_library(self.home, [("15100", "Assassin's Creed", "Assassins Creed", "1030")])
        self.assertFalse(self.eng.sync("15100")["changed"])
        self.assertEqual(self.game_status()["state"], "updating")

    def test_refuses_unknown_layout(self):
        # a "new version" without the expected glyphs must not be patched
        with open(self.game, "wb") as f:
            f.write(b"scimitar\0" + b"\0" * 100)
        result = self.eng.set_style("15100", "ps")
        self.assertFalse(result["ok"])
        self.assertIn("not supported", result["errors"][0])

    def test_profile_revision_triggers_reapply(self):
        profile = self.eng.profiles["15100"]
        self.eng.set_style("15100", "ps")
        self.assertFalse(self.eng.sync("15100")["changed"])
        profile.revision += 1
        self.assertEqual(self.game_status()["state"], "pending")
        result = self.eng.sync("15100")
        self.assertTrue(result["changed"], result)
        self.assertEqual(engine.read_trailer(self.game)["revision"], profile.revision)
        self.assertEqual(engine.read_trailer(self.game)["source_sha256"], self.original)

    def test_recovers_backup_after_lost_settings(self):
        self.eng.set_style("15100", "ps")
        os.remove(self.eng.config_path)
        self.assertTrue(self.eng.set_style("15100", "original")["ok"])
        self.assertEqual(engine.sha256_file(self.game), self.original)


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def fake_charlie_exe(path, tail=b"", with_pattern=True, variant=0):
    """A stand-in for CharlieMurder.exe: filler, the IL of UpdateInput's pad-type store, filler."""
    il = (b"\x6f\x11\x22\x33\x44\x2c\x0a"            # callvirt Equals ; brfalse.s
          b"\x7e\xaa\xbb\xcc\x04\x06\x17\x9e\x2b\x54"   # ldsfld T ; ldloc.0 ; ldc.i4.1 ; stelem.i4 ; br.s
          b"\x7e\xaa\xbb\xcc\x04\x06\x16\x9e\x2b\x4a")  # ldsfld T ; ldloc.0 ; ldc.i4.0 ; stelem.i4 ; br.s
    guids = "4c05c405".encode("utf-16le") + b"\0\0" + "4c05cc09".encode("utf-16le")
    with open(path, "wb") as f:
        f.write(b"MZ" + bytes((i * 13 + variant) & 255 for i in range(3000)))
        f.write(guids + (il if with_pattern else b"") + bytes(range(200)) + tail)


class CharlieMurderTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        common = make_library(self.home, [("405290", "Charlie Murder", "Charlie Murder", "4")])
        self.game = os.path.join(common, "Charlie Murder", "CharlieMurder.exe")
        fake_charlie_exe(self.game)
        self.original = engine.sha256_file(self.game)
        self.proc = os.path.join(self.home, "proc")
        os.makedirs(self.proc)
        self.eng = engine.Engine(
            home=self.home,
            settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"),
            glyph_dir=GLYPH_DIR,
            profiles={"405290": PROFILES["405290"]},
            proc_root=self.proc,
        )

    def tearDown(self):
        shutil.rmtree(self.home)

    def status(self):
        return next(g for g in self.eng.list_games() if g["appid"] == "405290")

    def stores(self):
        """(value stored for a DualShock, value stored for any other pad)."""
        data = read_bytes(self.game)
        m = re.search(rb"\x6f....\x2c.\x7e(....)\x06[\x16\x17]\x9e\x2b.\x7e\1\x06[\x16\x17]\x9e",
                      data, re.DOTALL)
        return data[m.start() + 13], data[m.start() + 23]

    def test_original_is_automatic(self):
        self.assertEqual(self.stores(), (0x17, 0x16))
        self.assertIn("DualShock", self.status()["original_hint"])

    def test_ps_forces_playstation_for_every_pad(self):
        self.assertTrue(self.eng.set_style("405290", "ps")["ok"])
        self.assertEqual(self.stores(), (0x17, 0x17))
        self.assertEqual(self.status()["state"], "ok")
        self.assertEqual(engine.read_trailer(self.game)["style"], "ps")

    def test_xbox_forces_xbox_for_every_pad(self):
        self.eng.set_style("405290", "xbox")
        self.assertEqual(self.stores(), (0x16, 0x16))

    def test_switch_never_stacks_and_restore_is_exact(self):
        self.eng.set_style("405290", "ps")
        self.eng.set_style("405290", "xbox")
        self.assertEqual(self.stores(), (0x16, 0x16))
        self.eng.set_style("405290", "original")
        self.assertEqual(engine.sha256_file(self.game), self.original)

    def test_only_one_byte_changes(self):
        before = read_bytes(self.game)
        self.eng.set_style("405290", "ps")
        after = read_bytes(self.game)
        body = after[:len(before)]
        self.assertEqual(sum(a != b for a, b in zip(before, body)), 1)

    def test_reapply_after_update(self):
        self.eng.set_style("405290", "ps")
        fake_charlie_exe(self.game, variant=5)  # Steam restores/updates the original
        make_library(self.home, [("405290", "Charlie Murder", "Charlie Murder", "4")], buildid="200")
        self.assertEqual(self.status()["state"], "pending")
        self.assertTrue(self.eng.sync_all("watch")[0]["changed"])
        self.assertEqual(self.stores(), (0x17, 0x17))
        self.assertFalse(self.eng.sync_all("watch")[0]["changed"])

    def test_refuses_build_without_the_expected_code(self):
        fake_charlie_exe(self.game, with_pattern=False)
        result = self.eng.set_style("405290", "ps")
        self.assertFalse(result["ok"])
        self.assertIn("not supported", result["errors"][0])
        self.assertIsNone(engine.read_trailer(self.game))

    def test_refuses_ambiguous_match(self):
        fake_charlie_exe(self.game)
        data = read_bytes(self.game)
        m = PROFILES["405290"].pattern.search(data)
        with open(self.game, "ab") as f:
            f.write(data[m.start():m.end()])
        self.assertFalse(self.eng.set_style("405290", "ps")["ok"])

    def test_real_pattern_ignores_unrelated_code(self):
        # same shape but storing 0/1 the other way round is a different method: no match
        data = bytes.fromhex("6f11223344 2c0a 7eaabbcc04 06 16 9e 2b54 7eaabbcc04 06 17 9e")
        self.assertIsNone(PROFILES["405290"].pattern.search(data))


class AdjustTest(unittest.TestCase):
    def test_flip_and_tint(self):
        # 1x2 icon: top red opaque, bottom transparent
        icon = bytes([255, 0, 0, 255, 0, 0, 0, 0])
        P = fx.ForgeGlyphProfile
        flipped = P._adjust(icon, 1, 2, {"flip_y": True})
        self.assertEqual(flipped, bytes([0, 0, 0, 0, 255, 0, 0, 255]))
        tinted = P._adjust(icon, 1, 2, {"tint": (255, 255, 255)})
        self.assertEqual(tinted[:4], bytes([255, 255, 255, 255]))
        self.assertEqual(tinted[7], 0)  # alpha untouched

    def test_flipped_texture_draws_icon_upside_down(self):
        d = tempfile.mkdtemp()
        try:
            alphas = {}
            for flip in (False, True):
                path = os.path.join(d, "DataPC_%d.forge" % flip)
                fx.build_forge(path)
                profile = fx.test_profile()
                profile.textures[0]["flip_y"] = flip
                profile.patch(path, "ps", engine.IconLoader(GLYPH_DIR))
                x0, y0, x1, y1 = fx.BOXES["head"]
                rgba = texture_rgba(path)
                alphas[flip] = [[rgba[(y * 64 + x) * 4 + 3] for x in range(x0, x1 + 1)]
                                for y in range(y0, y1 + 1)]
            self.assertNotEqual(alphas[False], alphas[True])  # the triangle is not symmetric
            self.assertEqual(alphas[True], alphas[False][::-1])
        finally:
            shutil.rmtree(d)


class ProfilesTest(unittest.TestCase):
    def test_bundled_icons_exist_for_every_profile(self):
        for profile in PROFILES.values():
            roles = {r for r, _ in profile.roles}
            for style, icons in profile.styles.items():
                self.assertEqual(set(icons), roles, (profile.appid, style))
                for name in icons.values():
                    self.assertTrue(os.path.exists(os.path.join(GLYPH_DIR, name + ".png")), name)
            for tex in getattr(profile, "textures", []):
                self.assertTrue(set(tex["boxes"]) <= roles)


if __name__ == "__main__":
    unittest.main()
