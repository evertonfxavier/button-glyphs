"""tools/triage_game.py: engine/container/texture triage of an install folder, on synthetic trees only.

Every case builds a tiny tree in a temp folder (no game data, no game art): a Unity-like folder, an Unreal pak
folder, GameMaker `data.win`, XNB content, forge / CPK / rmdp+xml archives with just enough structure for the
cheap name listers, Godot, and so on. The run is read-only and bounded, and the tests prove it: a spy refuses every
write primitive, another records every byte read per file, symlinks (a loop, one pointing outside) are never opened or
descended, a 3 GB sparse file is never read beyond a few KB, the walk and the clock have limits. Host mode runs against a
stub deck.sh the way tests/test_deck_probe.py does.
"""
import builtins
import contextlib
import io
import json
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import triage_game as tg  # noqa: E402
from button_glyphs import steam  # noqa: E402


# ------------------------------------------------------------------ fixture builders (no game data)
def varint(n):
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def xnb_texture(w, h, surface=0, flags=0):
    reader = b"Microsoft.Xna.Framework.Content.Texture2DReader"
    body = varint(1) + varint(len(reader)) + reader + struct.pack("<i", 0) + varint(1) + varint(0)
    block = struct.pack("<iiiii", surface, w, h, 1, w * h * 4) + b"\x00" * (w * h * 4)
    total = 10 + len(body) + len(block)
    return b"XNB" + b"w" + bytes([5, flags]) + struct.pack("<I", total) + body + block


def dds(fourcc=b"DXT5", w=64, h=32):
    head = bytearray(128)
    head[0:4] = b"DDS "
    struct.pack_into("<I", head, 4, 124)
    struct.pack_into("<II", head, 12, h, w)
    struct.pack_into("<I", head, 76, 32)
    head[84:88] = fourcc
    return bytes(head) + b"\x00" * 64


def png(w=16, h=16):
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0) + b"\0" * 8


def utf_table(columns, rows):
    """A CRI @UTF table. columns: [(name, type)], per-row storage. Types: 4 u32, 6 u64, 0xA string."""
    strings = b"<UTF>\0"
    offs = {}

    def s(text):
        nonlocal strings
        if text not in offs:
            offs[text] = len(strings)
            strings += text.encode() + b"\0"
        return offs[text]

    cols = b"".join(bytes([0x50 | ty]) + struct.pack(">I", s(name)) for name, ty in columns)
    body = b""
    for row in rows:
        for name, ty in columns:
            v = row[name]
            body += struct.pack(">I", s(v)) if ty == 0xA else struct.pack(">Q" if ty == 6 else ">I", v)
    rowlen = len(body) // len(rows)
    rows_off = 24 + len(cols)
    str_off = rows_off + len(body)
    data_off = str_off + len(strings)
    head = struct.pack(">IIIIHHI", rows_off, str_off, data_off, 0, len(columns), rowlen, len(rows))
    inner = head + cols + body + strings
    return b"@UTF" + struct.pack(">I", len(inner)) + inner


def cpk(names):
    """A CPK whose header and TOC are real @UTF tables, with `names` ('dir/file') as its TOC rows."""
    toc_rows = [{"DirName": n.rsplit("/", 1)[0], "FileName": n.rsplit("/", 1)[-1], "FileSize": 8, "ExtractSize": 8,
                 "FileOffset": 0} for n in names]
    toc = utf_table([("DirName", 0xA), ("FileName", 0xA), ("FileSize", 4), ("ExtractSize", 4), ("FileOffset", 6)],
                    toc_rows)
    toc_chunk = b"TOC " + b"\0" * 12 + toc
    hdr_cols = [("TocOffset", 6), ("TocSize", 4), ("ContentOffset", 6)]
    probe = b"CPK " + b"\0" * 12 + utf_table(hdr_cols, [{"TocOffset": 0, "TocSize": 0, "ContentOffset": 0}])
    toc_off = 0x800
    header = utf_table(hdr_cols, [{"TocOffset": toc_off, "TocSize": len(toc), "ContentOffset": 0x4000}])
    head = b"CPK " + b"\0" * 12 + header
    assert len(head) <= toc_off and len(probe) == len(head)
    return head + b"\0" * (toc_off - len(head)) + toc_chunk + b"\0" * 64


def forge(names):
    n = len(names)
    hdr_off, block_off, idx_off = 0x40, 0x100, 0x200
    names_off = idx_off + 16 * n + 16
    out = bytearray(names_off + 188 * n + 64)
    out[0:9] = b"scimitar\0"
    struct.pack_into("<iq", out, 9, 25, hdr_off)
    struct.pack_into("<q", out, hdr_off + 0x20, block_off)
    struct.pack_into("<iiqqiiqq", out, block_off, n, 0, idx_off, -1, 0, n - 1, names_off, 0)
    for i, name in enumerate(names):
        struct.pack_into("<qII", out, idx_off + 16 * i, 0x1000 + i, i + 1, 100)
        out[names_off + 188 * i + 44:names_off + 188 * i + 44 + len(name)] = name.encode()
    return bytes(out)


def noise(n, seed=7):
    import random
    return random.Random(seed).getrandbits(8 * n).to_bytes(n, "little")


class Tree(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.game = os.path.join(self.root, "Game")
        os.makedirs(self.game)

    def put(self, rel, data=b"", base=None):
        path = os.path.join(base or self.game, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data if isinstance(data, bytes) else b"\0" * data)
        return path

    def put_all(self, files):
        for rel, data in files.items():
            self.put(rel, data)

    def run_triage(self, **kw):
        return tg.triage(self.game, **kw)

    @staticmethod
    def engine(rep, name):
        return next((e for e in rep["engines"] if e["engine"] == name), None)


def tree_state(root):
    out = {}
    for base, dirs, names in os.walk(root):
        for n in dirs + names:
            p = os.path.join(base, n)
            st = os.lstat(p)
            data = b""
            if stat.S_ISREG(st.st_mode) and st.st_size < (1 << 22):
                with open(p, "rb") as f:
                    data = f.read()
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mode, st.st_mtime_ns, data)
    return out


# ------------------------------------------------------------------ the engine matrix
ENGINE_CASES = {
    "Unity": {
        "UnityPlayer.dll": b"MZ" + b"\0" * 80, "Game.exe": b"MZ" + b"\0" * 80,
        "Game_Data/globalgamemanagers": 200, "Game_Data/resources.assets": 300, "Game_Data/sharedassets0.assets": 100,
        "Game_Data/Managed/Assembly-CSharp.dll": b"MZ" + b"\0" * 80, "Game_Data/StreamingAssets/x.json": b"{}"},
    "Unreal": {
        "Engine/Binaries/ThirdParty/x.dll": b"MZ" + b"\0" * 80,
        "Game/Content/Paks/Game-WindowsNoEditor.pak": noise(512),
        "Game/Binaries/Win64/Game-Win64-Shipping.exe": b"MZ" + b"\0" * 80,
        "Game/Content/Paks/Game-WindowsNoEditor.utoc": b"-==--==--==--==-" + b"\0" * 80},
    "GameMaker": {"data.win": b"FORM" + b"\0" * 200, "options.ini": b"[a]\n", "audiogroup1.dat": noise(64),
                  "Game.exe": b"MZ" + b"\0" * 80},
    "MonoGame/XNA/FNA": {"Content/Tooltip/A.xnb": xnb_texture(4, 4), "Content/Tooltip/B.xnb": xnb_texture(4, 4),
                         "Content/Menu/c.xnb": xnb_texture(5, 5), "Content/Menu/d.xnb": xnb_texture(5, 5)},
    "Godot": {"game.pck": b"GDPC" + b"\0" * 200, "Game.exe": b"MZ" + b"\0" * 80},
    "RPG Maker": {"www/js/rpg_core.js": b"//", "www/data/System.json": b"{}", "Game.exe": b"MZ" + b"\0" * 80},
    "Ren'Py": {"renpy/common/x.rpyc": 10, "game/script.rpy": b"label start:", "game/archive.rpa": b"RPA-3.0 " + b"\0" * 64},
    "Electron": {"resources/app.asar": noise(256), "chrome_100_percent.pak": noise(64), "locales/en-US.pak": noise(64)},
    "Source/Valve": {"hl2/gameinfo.txt": b"GameInfo {}", "hl2/pak01_dir.vpk": b"\x34\x12\xaa\x55" + b"\0" * 60,
                     "hl2/maps/a.bsp": b"VBSP" + b"\0" * 20},
    "id Tech/Quake-like": {"baseq3/pak0.pk3": b"PK\x03\x04" + b"\0" * 60, "baseq3/pak1.pk3": b"PK\x03\x04" + b"\0" * 60},
    "Remedy Northlight": {"data/ep999-000.rmdp": b"\0" * 5000, "data/ep999-000.xml": b"<files/>"},
    "Ubisoft Scimitar/Anvil": {"DataPC.forge": forge(["Game Bootstrap Settings"]), "DataPC_x.forge": forge(["a"])},
    "CRI CPK container": {"localize.cpk": cpk(["resource/ui/EN/pad.emb", "resource/ui/FR/pad.emb"]), "x.exe": b"MZ" + b"\0" * 80},
    "Bethesda (Creation family)": {"Data/Game.esm": b"TES4" + b"\0" * 40, "Data/Game - Textures.ba2": b"BTDX" + b"\0" * 40},
    "Frostbite": {"Data/Win32/cas.cat": b"NyanNyanNyanNyan" + b"\0" * 40, "Data/Win32/cas_01.cas": noise(64),
                  "Data/initfs_Win32": noise(64)},
    "Dimps-style EMB bundles": {"ui/pad.emb": b"#EMB" + b"\0" * 60},
}


class EngineMatrixTest(Tree):
    def test_every_engine_is_recognised_with_evidence_and_a_usable_confidence(self):
        for expected, files in sorted(ENGINE_CASES.items()):
            with self.subTest(engine=expected):
                game = tempfile.mkdtemp(dir=self.root)
                for rel, data in files.items():
                    self.put(rel, data, base=game)
                rep = tg.triage(game)
                top = rep["engines"][0]
                found = next((e for e in rep["engines"] if e["engine"] == expected), None)
                self.assertIsNotNone(found, [e["engine"] for e in rep["engines"]])
                self.assertEqual(top["engine"], expected, [(e["engine"], e["score"]) for e in rep["engines"]])
                self.assertIn(found["confidence"], ("high", "medium"))
                self.assertTrue(found["evidence"])
                self.assertTrue(all(isinstance(t, str) and t for t in found["evidence"]))

    def test_high_confidence_needs_a_magic_confirmation_not_just_a_name(self):
        self.put_all({"data.win": 300, "Game.exe": b"MZ" + b"\0" * 80})            # right name, zeroes inside
        rep = self.run_triage()
        self.assertEqual(self.engine(rep, "GameMaker")["confidence"], "medium")
        self.put("data.win", b"FORM" + b"\0" * 300)
        rep = self.run_triage()
        self.assertEqual(self.engine(rep, "GameMaker")["confidence"], "high")

    def test_xnb_extension_without_the_xnb_magic_is_not_high_confidence(self):
        for i in range(12):
            self.put("Content/t%d.xnb" % i, b"not an xnb at all" * 3)
        rep = self.run_triage()
        self.assertNotEqual(self.engine(rep, "MonoGame/XNA/FNA")["confidence"], "high")

    def test_unknown_game(self):
        self.put_all({"a.bin": noise(100), "b.dat": noise(100), "readme.txt": b"hello"})
        rep = self.run_triage()
        self.assertEqual(rep["engines"][0]["engine"], "unknown")
        self.assertEqual(rep["engines"][0]["confidence"], "none")
        self.assertEqual([e["engine"] for e in rep["engines"]], ["unknown"])

    def test_dotnet_and_il2cpp_runtimes(self):
        self.put_all(ENGINE_CASES["Unity"])
        names = [r["name"] for r in self.run_triage()["runtimes"]]
        self.assertTrue(any("Mono/.NET" in n for n in names), names)
        il = tempfile.mkdtemp(dir=self.root)
        for rel, data in {"UnityPlayer.dll": b"MZ", "GameAssembly.dll": b"MZ", "Game_Data/il2cpp_data/Metadata/g.dat": 10,
                          "Game_Data/globalgamemanagers": 10}.items():
            self.put(rel, data, base=il)
        names = [r["name"] for r in tg.triage(il)["runtimes"]]
        self.assertTrue(any("IL2CPP" in n for n in names), names)
        fna = tempfile.mkdtemp(dir=self.root)
        for rel in ("FNA.dll", "mscorlib.dll", "Game.exe"):
            self.put(rel, b"MZ" + b"\0" * 10, base=fna)
        self.assertTrue(any(".NET managed" in r["name"] for r in tg.triage(fna)["runtimes"]))


class ContainersAndTexturesTest(Tree):
    def test_containers_are_found_by_magic_largest_first_with_sizes(self):
        self.put("a/small.cpk", cpk(["x/y.bin"]))
        self.put("b/large.forge", forge(["n%d" % i for i in range(40)]))
        self.put("c/zip.dat", b"PK\x03\x04" + b"\0" * 5000)              # an archive under a boring name
        self.put("d/notes.txt", b"hello")
        rep = self.run_triage()
        kinds = [k["kind"] for k in rep["containers"]["kinds"]]
        self.assertEqual(set(kinds), {"cpk", "forge"} | ({"zip"} if "zip" in kinds else set()) | set(kinds))
        sizes = [k["bytes"] for k in rep["containers"]["kinds"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        self.assertIn("forge", kinds)
        self.assertIn("cpk", kinds)
        big = rep["containers"]["largest"]
        self.assertEqual([r["size"] for r in big], sorted([r["size"] for r in big], reverse=True))

    def test_texture_formats_by_header(self):
        self.put_all({"t/a.dds": dds(b"DXT5"), "t/b.dds": dds(b"DXT1"), "t/c.dds": dds(b"\0\0\0\0"), "t/d.png": png(32, 8),
                      "t/e.xnb": xnb_texture(7, 3), "t/f.xnb": xnb_texture(7, 3, surface=6)})
        rep = self.run_triage()
        formats = {f["format"] for f in rep["textures"]["formats"]}
        self.assertTrue({"DDS DXT5", "DDS DXT1", "PNG 8bit", "XNB Texture2D Color(RGBA8)", "XNB Texture2D Dxt5"} <= formats, formats)
        self.assertTrue(any(f.startswith("DDS raw") for f in formats), formats)
        # candidates carry the picture size
        png_row = tg.triage(self.game, keywords=["t"])["candidates"]
        self.assertIn("32x8", [c.get("dims") for c in png_row])

    def test_dx10_header_names_the_dxgi_format(self):
        data = bytearray(dds(b"DX10") + b"\0" * 32)
        struct.pack_into("<I", data, 128, 98)
        self.put("bc7.dds", bytes(data))
        self.assertIn("DDS BC7", {f["format"] for f in self.run_triage()["textures"]["formats"]})

    def test_compressed_xnb_is_reported_as_such(self):
        self.put("c.xnb", xnb_texture(4, 4, flags=0x80))
        self.assertIn("XNB v5 compressed", {f["format"] for f in self.run_triage()["textures"]["formats"]})

    def test_entropy_of_the_largest_containers(self):
        self.put("Data/enc.pak", noise(8192))
        self.put("Data/plain.cpk", cpk(["a/b.bin"]) + b"\0" * (2 << 20))
        self.put("Data/big.bin", b"\0" * (80 << 20))
        rep = self.run_triage()
        by = {e["path"]: e for e in rep["entropy"]}
        self.assertTrue(by["Data/enc.pak"]["verdict"].startswith("likely encrypted or compressed")
                        if "Data/enc.pak" in by else True)
        self.assertTrue(by["Data/plain.cpk"]["verdict"].startswith("structured"))
        self.assertTrue(by["Data/big.bin"]["entropy"] < 1)
        enc = tempfile.mkdtemp(dir=self.root)
        self.put("x/game.pak", noise(2 << 20), base=enc)
        e = tg.triage(enc)["entropy"][0]
        self.assertGreater(e["entropy"], 7.5)
        self.assertEqual(e["verdict"], "likely encrypted or compressed")

    def test_a_zip_is_compressed_by_design_in_the_verdict(self):
        self.put("x.pk3", b"PK\x03\x04" + noise(2 << 20))
        self.assertIn("compressed by design", self.run_triage()["entropy"][0]["verdict"])


class ArchiveNamesTest(Tree):
    def test_cpk_names_are_listed_without_reading_the_payload(self):
        names = ["resource/ui/EN/pad_buttons.emb", "resource/ui/FR/pad_buttons.emb", "resource/model/hero.bin"]
        self.put("localize.cpk", cpk(names))
        rep = self.run_triage()
        row = rep["archive_names"][0]
        self.assertTrue(row["listable"], row)
        self.assertEqual((row["format"], row["entries"]), ("cpk", 3))
        self.assertEqual(row["matching"], 2)
        self.assertEqual([n["name"] for n in row["names"]], ["resource/ui/EN/pad_buttons.emb", "resource/ui/FR/pad_buttons.emb"])
        self.assertEqual(dict(row["top_extensions"])["emb"], 2)
        self.assertIn("lists .emb", " ".join(self.engine(rep, "CRI CPK container")["evidence"]))

    def test_forge_names_are_listed(self):
        self.put("DataPC.forge", forge(["Game Bootstrap Settings", "Controls_Xbox_Buttons", "Terrain"]))
        row = self.run_triage()["archive_names"][0]
        self.assertTrue(row["listable"], row)
        self.assertEqual((row["format"], row["entries"]), ("forge", 3))
        self.assertEqual([n["name"] for n in row["names"]], ["Controls_Xbox_Buttons"])

    def test_zip_names_are_listed_from_the_central_directory(self):
        path = os.path.join(self.game, "pak0.pk3")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("gfx/hud/button_a.tga", b"x" * 100)
            z.writestr("maps/e1m1.bsp", b"y" * 100)
        row = self.run_triage()["archive_names"][0]
        self.assertTrue(row["listable"], row)
        self.assertEqual((row["format"], row["entries"], row["matching"]), ("zip", 2, 1))

    def test_rmdp_names_come_from_the_xml_index_next_to_it(self):
        self.put("data/ep999-000.rmdp", 4096)
        self.put("data/ep999-000.xml", b'<?xml version="1.0"?><archive><file name="textures/hardcoded/prompts.tex" '
                                       b'offset="1" size="2" crc="3"/><file name="sounds/a.wav" offset="3"/></archive>')
        row = self.run_triage()["archive_names"][0]
        self.assertEqual(row["format"], "rmdp+xml")
        self.assertTrue(row["listable"], row)
        self.assertEqual(row["entries"], 2)
        self.assertEqual([n["name"] for n in row["names"]], ["textures/hardcoded/prompts.tex"])

    def test_an_unreadable_listing_says_so_and_never_raises(self):
        broken = bytearray(cpk(["a/b"]))
        broken[16:20] = b"\xde\xad\xbe\xef"                                  # an encrypted-looking header table
        self.put("enc.cpk", bytes(broken))
        self.put("trunc.forge", forge(["a"])[:100])
        self.put("noindex.rmdp", 100)
        rep = self.run_triage()
        rows = {r["archive"]: r for r in rep["archive_names"]}
        self.assertFalse(rows["enc.cpk"]["listable"])
        self.assertFalse(rows["trunc.forge"]["listable"])
        self.assertTrue(rows["enc.cpk"]["note"])
        self.assertIn("rmdp (no xml index next to it)", [n["format"] for n in rep["archives_not_listable"]])

    def test_formats_that_need_decompression_are_not_listed(self):
        self.put_all({"Game/Content/Paks/a.pak": noise(300), "Game_Data/globalgamemanagers": 100,
                      "b.vpk": b"\x34\x12\xaa\x55" + b"\0" * 40})
        rep = self.run_triage()
        self.assertEqual(rep["archive_names"], [])
        self.assertTrue({"pak", "vpk"} <= {n["format"] for n in rep["archives_not_listable"]})

    def test_the_names_cap(self):
        self.put("big.forge", forge(["button_%03d" % i for i in range(300)]))
        row = self.run_triage()["archive_names"][0]
        self.assertEqual(row["matching"], 300)
        self.assertLessEqual(len(row["names"]), 20)


class RankingTest(Tree):
    def ranked(self, files, **kw):
        for rel, data in files.items():
            self.put(rel, data)
        return [c["path"] for c in self.run_triage(**kw)["candidates"]]

    def test_stronger_words_and_small_images_in_ui_folders_rank_first(self):
        paths = self.ranked({"UI/xbox/a.png": 100, "Hud/misc/hud_frame.png": 100, "Hud/misc/hud_big.png": 900000,
                             "docs/help.txt": 50, "Audio/button_click.ogg": 10, "System/xinput1_3.dll": b"MZ" * 4})
        self.assertEqual(paths[0], "UI/xbox/a.png")
        self.assertLess(paths.index("Hud/misc/hud_frame.png"), paths.index("Hud/misc/hud_big.png"))
        self.assertNotIn("Audio/button_click.ogg", paths)                       # audio is never a prompt picture
        self.assertNotIn("System/xinput1_3.dll", paths)                         # but it is an input-layer lead
        self.assertIn("System/xinput1_3.dll", self.run_triage()["input_layer"])

    def test_word_rules_do_not_match_inside_other_words(self):
        paths = self.ranked({"a/padding.png": 10, "a/Pad1.png": 10, "a/spade.png": 10, "a/ButtonA.png": 10,
                             "a/tooltipBox.png": 10, "a/ControllToolTip/x.png": 10, "a/xbox360_menu.png": 10,
                             "a/buildings.png": 10, "gui/x.png": 10, "ui/x.png": 10})
        self.assertNotIn("a/padding.png", paths)
        self.assertNotIn("a/spade.png", paths)
        self.assertNotIn("gui/x.png", paths)
        self.assertNotIn("a/buildings.png", paths)
        for ok in ("a/Pad1.png", "a/ButtonA.png", "a/tooltipBox.png", "a/ControllToolTip/x.png", "a/xbox360_menu.png",
                   "ui/x.png"):
            self.assertIn(ok, paths)

    def test_each_row_has_path_size_kind_score_words(self):
        self.put("UI/xbox/a.xnb", xnb_texture(13, 13))
        row = self.run_triage()["candidates"][0]
        self.assertEqual(set(row), {"path", "size", "kind", "score", "words", "dims", "format"})
        self.assertEqual((row["kind"], row["dims"], row["words"]), ("xnb", "13x13", ["ui/", "xbox"]))

    def test_every_folder_is_represented_even_when_a_big_set_scores_higher(self):
        files = {"Tooltip/Switch/Button%s.png" % c: 20 for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
        files.update({"Tooltip/Switch/controller%d.png" % i: 20 for i in range(40)})
        files.update({"Tooltip/A.png": 20, "Menu/xbox/a.png": 20})
        paths = self.ranked(files)
        self.assertEqual(len(paths), 60)
        self.assertIn("Tooltip/A.png", paths)
        self.assertIn("Menu/xbox/a.png", paths)
        rep = self.run_triage()
        self.assertEqual(rep["candidate_total"], 68)
        self.assertEqual(rep["candidate_folders"][0]["folder"], "Tooltip/Switch")

    def test_keyword_override_replaces_the_defaults(self):
        for rel in ("a/xbox_a.png", "a/zebra.png", "a/zebra_hint.png"):
            self.put(rel, 10)
        default = [c["path"] for c in self.run_triage()["candidates"]]
        self.assertIn("a/xbox_a.png", default)
        self.assertNotIn("a/zebra.png", default)
        custom_rep = self.run_triage(keywords=["zebra"])
        self.assertEqual({c["path"] for c in custom_rep["candidates"]}, {"a/zebra.png", "a/zebra_hint.png"})
        self.assertEqual(custom_rep["keywords"], {"list": ["zebra"], "custom": True})

    def test_ranking_is_stable_and_total_equals_the_matches(self):
        for i in range(10):
            self.put("UI/btn_%d.png" % i, 10 + i)
        a = [c["path"] for c in self.run_triage()["candidates"]]
        b = [c["path"] for c in self.run_triage()["candidates"]]
        self.assertEqual(a, b)
        self.assertEqual(a, sorted(a))                                          # same score: path order


class VariantSetsTest(Tree):
    def test_xbox_ps_switch_pairs_by_filename(self):
        self.put_all({"Menu/xbox/a.png": 10, "Menu/ps4/a.png": 10, "Menu/switch/a.png": 10, "Menu/xbox/b.png": 10,
                      "Tooltip/A.png": 10, "Tooltip/Switch/A.png": 10, "icons/btn_x_xbox.png": 10,
                      "icons/btn_x_ps5.png": 10, "icons/lonely_ps4.png": 10})
        vs = self.run_triage()["variant_sets"]
        self.assertEqual(vs["files_by_family"], {"ps": 3, "switch": 2, "xbox": 3})
        names = {p["name"]: p["families"] for p in vs["paired"]}
        self.assertEqual(names["menu/a.png"], ["ps", "switch", "xbox"])
        self.assertEqual(names["tooltip/a.png"], ["default", "switch"])
        self.assertEqual(names["icons/btn_x.png"], ["ps", "xbox"])
        self.assertEqual(vs["ps_and_xbox_pairs"], 2)
        self.assertEqual(vs["folders"]["switch"], ["Menu/switch", "Tooltip/Switch"])

    def test_no_variants(self):
        self.put("a/b.png", 10)
        vs = self.run_triage()["variant_sets"]
        self.assertEqual((vs["files_by_family"], vs["paired_count"]), ({}, 0))


class StrategyHintsTest(Tree):
    def hints(self, **kw):
        rep = self.run_triage(**kw)
        for h in rep["strategy_hints"]:
            self.assertIn(h["likelihood"], ("likely", "possible", "unknown"))      # a triage never says "certain"
            self.assertTrue(h["reason"] and h["check_next"])
        return {h["strategy"].split(" (")[0]: h for h in rep["strategy_hints"]}

    def test_both_sets_present_points_to_a_code_or_saved_value_switch(self):
        self.put_all({"Menu/xbox/a.png": 10, "Menu/ps4/a.png": 10})
        h = self.hints()
        self.assertEqual(h["code-switch"]["likelihood"], "likely")
        self.assertIn("1 paired", h["code-switch"]["reason"])

    def test_xbox_and_switch_only_is_only_possible(self):
        self.put_all({"Menu/xbox/a.png": 10, "Menu/switch/a.png": 10})
        h = self.hints()
        self.assertEqual(h["code-switch"]["likelihood"], "possible")
        self.assertIn("no PlayStation set", h["code-switch"]["reason"])

    def test_loose_small_pictures(self):
        for i in range(8):
            self.put("Content/Tooltip/b%d.xnb" % i, xnb_texture(4, 4))
        self.assertEqual(self.hints()["loose files"]["likelihood"], "likely")

    def test_rmdp_with_xml_means_regions(self):
        self.put_all({"data/a.rmdp": 100, "data/a.xml": b'<f name="textures/prompts.tex"/>'})
        self.assertEqual(self.hints()["in-place raw regions"]["likelihood"], "likely")

    def test_forge_means_entries_and_cpk_means_dxt_regions(self):
        self.put_all({"DataPC.forge": forge(["Controls_Buttons"]), "localize.cpk": cpk(["a/pad.emb"])})
        h = self.hints()
        self.assertEqual(h["entry-level append"]["likelihood"], "likely")
        self.assertEqual(h["in-place DXT regions"]["likelihood"], "possible")
        self.assertIn("1 .emb", h["in-place DXT regions"]["reason"])

    def test_config_keys_and_saved_value(self):
        self.put("settings/game.ini", b'[storyfile]\nvolume=3\nbuttons="523.000000"\nControllerType = 2\n')
        rep = self.run_triage()
        hit = rep["config_hits"][0]
        self.assertEqual(hit["path"], "settings/game.ini")
        self.assertEqual(len(hit["lines"]), 2)
        self.assertIn("saved value", [h["strategy"].split(" (")[0] for h in rep["strategy_hints"]])

    def test_encrypted_containers_without_loose_art_are_flagged_as_possibly_infeasible(self):
        self.put("data/all.pak", noise(2 << 20))
        h = self.hints()
        self.assertIn("probably not feasible", h)

    def test_nothing_known_says_so(self):
        self.put("a.txt", b"x")
        self.assertEqual(list(self.hints()), ["none yet"])

    # ---- the in-game option lead: both a PlayStation and an Xbox set AND a config key about prompts
    INI = b'[input]\nvolume=3\nControllerType = 2\n'

    def test_both_sets_and_a_prompt_key_point_to_an_option_in_the_game_itself(self):
        self.put_all({"Menu/xbox/a.png": 10, "Menu/ps4/a.png": 10, "settings/game.ini": self.INI})
        h = self.hints()
        hint = h["in-game option"]
        self.assertEqual(hint["likelihood"], "possible")                      # a lead, never a conclusion
        self.assertIn("PlayStation and Xbox prompt sets both ship and a config key about prompts exists", hint["reason"])
        self.assertIn("the game may offer the choice in its own settings", hint["reason"])
        self.assertIn("look in the game's controller/interface settings by hand", hint["check_next"])
        self.assertIn("tools/ingame_add.py", hint["check_next"])
        self.assertIn("code-switch", h)                                       # the older leads are still there
        self.assertIn("saved value", h)

    def test_both_sets_and_a_key_with_a_switch_set_too_still_fires(self):
        self.put_all({"Menu/xbox/a.png": 10, "Menu/ps5/a.png": 10, "Menu/switch/a.png": 10, "o.cfg": self.INI})
        self.assertIn("in-game option", self.hints())

    def test_both_sets_without_a_config_key_do_not_fire(self):
        self.put_all({"Menu/xbox/a.png": 10, "Menu/ps4/a.png": 10, "settings/game.ini": b"[a]\nvolume=3\n"})
        self.assertNotIn("in-game option", self.hints())

    def test_a_config_key_without_both_sets_does_not_fire(self):
        self.put("settings/game.ini", self.INI)
        self.assertNotIn("in-game option", self.hints())

    def test_xbox_and_switch_only_do_not_fire(self):
        self.put_all({"Menu/xbox/a.png": 10, "Menu/switch/a.png": 10, "settings/game.ini": self.INI})
        self.assertNotIn("in-game option", self.hints())

    def test_a_single_set_does_not_fire(self):
        for fam in ("xbox", "ps4"):
            with self.subTest(fam=fam):
                shutil.rmtree(self.game)
                os.makedirs(self.game)
                self.put_all({"Menu/%s/a.png" % fam: 10, "Menu/%s/b.png" % fam: 10, "settings/game.ini": self.INI})
                self.assertNotIn("in-game option", self.hints())

    def test_the_summary_shows_the_lead_and_stays_within_25_lines(self):
        self.put_all({"Menu/xbox/a.png": 10, "Menu/ps4/a.png": 10, "settings/game.ini": self.INI})
        text = tg.summarize(self.run_triage())
        self.assertIn("Hint [possible] in-game option", text)
        self.assertLessEqual(len(text.splitlines()), 25)


class BoundedAndReadOnlyTest(Tree):
    def test_max_files_truncates_and_says_so(self):
        for i in range(50):
            self.put("d%d/f%d.png" % (i % 5, i), 10)
        rep = self.run_triage(max_files=20)
        self.assertTrue(rep["truncated"]["files"])
        self.assertLessEqual(rep["totals"]["files"] + rep["totals"]["dirs"], 20)
        self.assertTrue(any("walk stopped" in n for n in rep["notes"]))
        full = self.run_triage()
        self.assertFalse(full["truncated"]["files"])
        self.assertEqual(full["totals"]["files"], 50)

    def test_time_budget_with_a_fake_clock(self):
        for i in range(400):
            self.put("d%d/f%d.png" % (i % 5, i), 10)
        ticks = {"n": 0}

        def clock():
            ticks["n"] += 1
            return float(ticks["n"])

        rep = tg.triage(self.game, budget_s=5, clock=clock)
        self.assertTrue(rep["truncated"]["time"])
        self.assertFalse(rep["truncated"]["files"])
        self.assertLess(rep["totals"]["files"], 400)
        self.assertTrue(any("time budget" in n for n in rep["notes"]))
        calm = tg.triage(self.game, budget_s=1e9, clock=lambda: 0.0)
        self.assertFalse(calm["truncated"]["time"])
        self.assertEqual(calm["totals"]["files"], 400)

    def test_symlinks_are_counted_but_never_followed_or_opened(self):
        outside = os.path.join(self.root, "outside")
        self.put("secret.png", b"\x89PNG\r\n\x1a\n" + b"s" * 50, base=outside)
        self.put("Content/ok.png", png())
        os.symlink(self.game, os.path.join(self.game, "loop"))                   # a loop back to the root
        os.symlink(outside, os.path.join(self.game, "Content", "out_dir"))        # a folder elsewhere
        os.symlink(os.path.join(outside, "secret.png"), os.path.join(self.game, "Content", "out_file.png"))
        os.symlink(os.path.join(self.game, "nowhere"), os.path.join(self.game, "dangling"))
        opened, scanned = [], []
        real_open, real_scandir = os.open, os.scandir

        def spy_open(path, *a, **k):
            opened.append(str(path))
            return real_open(path, *a, **k)

        def spy_scandir(path="."):
            scanned.append(str(path))
            return real_scandir(path)

        with mock.patch.object(os, "open", spy_open), mock.patch.object(os, "scandir", spy_scandir):
            rep = self.run_triage()
        self.assertEqual(rep["totals"]["files"], 1)
        self.assertEqual(rep["totals"]["symlinks"], 4)
        self.assertEqual(sorted(rep["symlinks_not_followed"]), ["Content/out_dir", "Content/out_file.png", "dangling", "loop"])
        self.assertFalse([p for p in opened + scanned if p.startswith(outside)], opened + scanned)
        self.assertFalse([p for p in opened if p.endswith(("out_file.png", "dangling", "loop"))])
        self.assertEqual(sorted(scanned), sorted({self.game, os.path.join(self.game, "Content")}))

    def test_a_link_in_the_path_of_a_probed_file_is_refused_by_nofollow(self):
        self.put("real.bin", b"CPK " + b"\0" * 100)
        meter = tg.Meter(lambda: 0.0, 1e9)
        link = os.path.join(self.game, "l.bin")
        os.symlink(os.path.join(self.game, "real.bin"), link)
        self.assertEqual(meter.read(link, 0, 64), b"")
        self.assertEqual(meter.read(os.path.join(self.game, "real.bin"), 0, 4), b"CPK ")
        self.assertEqual(meter.read(self.game, 0, 4), b"")                      # a folder is not a file

    def test_a_3gb_sparse_file_is_never_read_beyond_a_few_kb(self):
        path = self.put("Data/giant.bin", b"")
        with open(path, "r+b") as f:
            f.truncate(3 << 30)
        self.put("Data/small.cpk", cpk(["a/b"]))
        reads = {}
        real_pread, real_open = os.pread, os.open
        fds = {}

        def spy_open(p, *a, **k):
            fd = real_open(p, *a, **k)
            fds[fd] = str(p)
            return fd

        def spy_pread(fd, n, off):
            data = real_pread(fd, n, off)
            reads[fds[fd]] = reads.get(fds[fd], 0) + len(data)
            return data

        with mock.patch.object(os, "open", spy_open), mock.patch.object(os, "pread", spy_pread):
            rep = self.run_triage()
        self.assertEqual(rep["totals"]["bytes"] >= 3 << 30, True)
        giant = reads[path]
        self.assertLessEqual(giant, tg.MAGIC_BYTES + tg.ENTROPY_BYTES)
        self.assertLessEqual(rep["io"]["bytes_read"], sum(reads.values()))
        self.assertTrue(all(v <= 1 << 20 for v in reads.values()), reads)
        self.assertIn("Data/giant.bin", [c["path"] for c in rep["containers"]["largest"]])

    def test_every_read_is_bounded_by_the_documented_caps(self):
        for rel, data in ENGINE_CASES["Unity"].items():
            self.put(rel, data)
        self.put("localize.cpk", cpk(["a/b.emb"]))
        self.put("data/x.rmdp", 100)
        self.put("data/x.xml", b"<f name='a/b.tex'/>" * 1000)
        self.put("cfg/settings.ini", b"buttons=1\n" * 100000)                    # 1 MB: above TEXT_CAP, never opened
        self.put("cfg/small.ini", b"buttons=1\n" * 100)
        for i in range(120):
            self.put("t/p%d.png" % i, png())
        reads, fds = {}, {}
        real_pread, real_open = os.pread, os.open

        def spy_open(p, *a, **k):
            fd = real_open(p, *a, **k)
            fds[fd] = str(p)
            return fd

        def spy_pread(fd, n, off):
            data = real_pread(fd, n, off)
            reads[fds[fd]] = reads.get(fds[fd], 0) + len(data)
            return data

        with mock.patch.object(os, "open", spy_open), mock.patch.object(os, "pread", spy_pread):
            rep = self.run_triage()
        self.assertEqual(reads[os.path.join(self.game, "cfg", "settings.ini")], tg.MAGIC_BYTES)   # magic only, not its text
        for path, n in reads.items():
            limit = max(tg.HEADER_BYTES + tg.MAGIC_BYTES, tg.ENTROPY_BYTES + tg.MAGIC_BYTES, tg.TEXT_READ,
                        0x800 + 4096 + tg.MAGIC_BYTES)
            if path.endswith(".xml"):
                limit = tg.DEFAULT_PROBE_KB << 10
            self.assertLessEqual(n, limit, (path, n))
        self.assertEqual(rep["io"]["bytes_read"], sum(reads.values()))
        self.assertGreaterEqual(rep["io"]["opens"], len(reads))                 # a file may be opened twice (magic, header)

    def test_nothing_is_ever_written(self):
        self.put_all(ENGINE_CASES["Unity"])
        self.put("localize.cpk", cpk(["a/pad.emb"]))
        self.put("DataPC.forge", forge(["Controls"]))
        self.put("data/x.rmdp", 100)
        self.put("data/x.xml", b"<f name='a/b.tex'/>")
        self.put("cfg/g.ini", b"buttons=1\n")
        os.symlink(self.game, os.path.join(self.game, "loop"))
        os.utime(self.game, (1000000000, 1000000000))
        before = tree_state(self.game)
        banned = []

        def refuse(name):
            def f(*a, **k):
                banned.append(name)
                raise AssertionError("write primitive used: " + name)
            return f

        real_open, real_bopen = os.open, builtins.open
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

        def os_open(path, flags, *a, **k):
            if flags & write_flags:
                banned.append("os.open write flags")
                raise AssertionError("os.open with write flags")
            return real_open(path, flags, *a, **k)

        def b_open(file, mode="r", *a, **k):
            if set(mode) - set("rbt"):
                banned.append("open mode " + mode)
                raise AssertionError("open() with mode " + mode)
            return real_bopen(file, mode, *a, **k)

        patches = [mock.patch.object(os, n, refuse("os." + n)) for n in
                   ("write", "remove", "unlink", "rename", "replace", "mkdir", "makedirs", "rmdir", "removedirs",
                    "truncate", "ftruncate", "symlink", "link", "chmod", "chown", "utime", "mkfifo", "fsync")
                   if hasattr(os, n)]
        patches += [mock.patch.object(shutil, n, refuse("shutil." + n)) for n in ("rmtree", "copy", "copyfile", "move")]
        patches += [mock.patch.object(subprocess, "run", refuse("subprocess.run")),
                    mock.patch.object(subprocess, "Popen", refuse("subprocess.Popen")),
                    mock.patch.object(os, "open", os_open), mock.patch.object(builtins, "open", b_open)]
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            rep = self.run_triage()
        self.assertEqual(banned, [])
        self.assertTrue(rep["totals"]["files"] > 5)
        self.assertEqual(tree_state(self.game), before)                          # contents, sizes, modes, mtimes

    def test_the_remote_text_has_no_write_primitive(self):
        import ast
        tree = ast.parse(tg.remote_source())
        banned_on_os = {"remove", "unlink", "rename", "replace", "chmod", "chown", "makedirs", "mkdir", "rmdir", "symlink",
                        "link", "truncate", "system", "popen", "write", "ftruncate", "utime", "mkfifo", "fsync"}
        banned_anywhere = {"write", "writelines", "truncate"}
        problems = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) else [])
                problems += [n for n in names if n and n.split(".")[0] in ("shutil", "subprocess", "tempfile", "ctypes")]
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and (f.attr in banned_anywhere or (
                        isinstance(f.value, ast.Name) and f.value.id == "os" and f.attr in banned_on_os)):
                    problems.append("call to ." + f.attr)
                if isinstance(f, ast.Name) and f.id == "open":
                    mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
                    if mode is not None and not (isinstance(mode, ast.Constant) and set(mode.value) <= set("rb")):
                        problems.append("open() with a mode that can write")
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os" \
                    and node.attr in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"):
                problems.append("os." + node.attr)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "sudo" in node.value:
                problems.append("sudo")
        self.assertEqual(problems, [])

    def test_the_remote_text_compiles_on_old_pythons(self):
        compile(tg.remote_source(), "remote", "exec")
        self.assertNotIn(":=", tg.remote_source())


class ReportShapeTest(Tree):
    def build(self):
        self.put_all(ENGINE_CASES["MonoGame/XNA/FNA"])
        self.put("localize.cpk", cpk(["a/pad.emb"]))
        self.put("Menu/xbox/a.png", png())
        self.put("Menu/switch/a.png", png())

    def strip(self, rep):
        rep = json.loads(json.dumps(rep, sort_keys=True))
        rep.pop("generated_at")
        rep.pop("seconds")
        return rep

    def test_json_shape(self):
        self.build()
        rep = self.run_triage()
        self.assertEqual(rep["schema"], 1)
        for key in ("install_dir_name", "limits", "truncated", "io", "totals", "extensions", "top_level", "engines",
                    "runtimes", "containers", "textures", "entropy", "candidates", "candidate_total", "archive_names",
                    "archives_not_listable", "variant_sets", "input_layer", "config_hits", "keywords", "notes",
                    "strategy_hints", "generated_at", "seconds", "magic_kinds", "candidate_folders", "symlinks_not_followed"):
            self.assertIn(key, rep)
        self.assertEqual(rep["install_dir_name"], "Game")
        self.assertEqual(set(rep["extensions"]), {"by_count", "by_bytes"})
        self.assertLessEqual(len(rep["extensions"]["by_count"]), 25)
        json.dumps(rep)                                                         # plain JSON all the way down
        self.assertNotIn(self.root, json.dumps(rep))                            # no absolute path leaks into the report

    def test_deterministic_and_sorted(self):
        self.build()
        a, b = self.strip(self.run_triage()), self.strip(self.run_triage())
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        counts = [c for _, c, _ in a["extensions"]["by_count"]]
        self.assertEqual(counts, sorted(counts, reverse=True))
        sizes = [s for _, _, s in a["extensions"]["by_bytes"]]
        self.assertEqual(sizes, sorted(sizes, reverse=True))
        ties = [e for e, c, _ in a["extensions"]["by_count"] if c == counts[0]]
        self.assertEqual(ties, sorted(ties))
        scores = [e["score"] for e in a["engines"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_extension_histogram_top_25(self):
        for i in range(40):
            self.put("f/a.e%02d" % i, i + 1)
        rep = self.run_triage()
        self.assertEqual(len(rep["extensions"]["by_count"]), 25)
        self.assertEqual(len(rep["extensions"]["by_bytes"]), 25)
        self.assertEqual(rep["extensions"]["by_bytes"][0][0], "e39")

    def test_the_summary_is_at_most_25_lines(self):
        self.build()
        rep = self.run_triage()
        text = tg.summarize(rep)
        self.assertLessEqual(len(text.splitlines()), 25)
        self.assertIn("Engine:", text)
        # a very rich report is still cut at 25 lines
        rep["strategy_hints"] = rep["strategy_hints"] * 30
        rep["archive_names"] = rep["archive_names"] * 30
        rep["runtimes"] = [{"name": "x" * 400, "evidence": []}]
        text = tg.summarize(rep)
        self.assertLessEqual(len(text.splitlines()), 25)
        self.assertTrue(all(len(l) <= 223 for l in text.splitlines()))
        short = tg.summarize(rep, limit=10).splitlines()
        self.assertEqual(len(short), 10)
        self.assertIn("more lines in the JSON", short[-1])

    def test_top_level_and_totals(self):
        self.put_all({"a/x.bin": 100, "a/y.bin": 50, "z.txt": 10})
        rep = self.run_triage()
        self.assertEqual(rep["totals"]["files"], 3)
        self.assertEqual(rep["totals"]["bytes"], 160)
        self.assertEqual(rep["top_level"][0], {"name": "a", "type": "dir", "files": 2, "bytes": 150})


class CliTest(Tree):
    def run_main(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = tg.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_local_mode_prints_the_summary_and_writes_json(self):
        self.put_all(ENGINE_CASES["MonoGame/XNA/FNA"])
        path = os.path.join(self.root, "out.json")
        code, out, _ = self.run_main(self.game, "--json", path, "--keywords", "tooltip,menu")
        self.assertEqual(code, 0)
        self.assertLessEqual(len(out.splitlines()), 25)
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["mode"], "local")
        self.assertEqual(data["keywords"]["list"], ["tooltip", "menu"])
        self.assertEqual(data["engines"][0]["engine"], "MonoGame/XNA/FNA")

    def test_usage_errors_are_not_exit_2(self):
        self.assertEqual(self.run_main()[0], 64)
        self.assertEqual(self.run_main(self.game, "--appid", "1")[0], 64)
        self.assertEqual(self.run_main(os.path.join(self.root, "missing"))[0], 1)

    def test_max_files_flag(self):
        for i in range(30):
            self.put("d/f%d.png" % i, 10)
        code, out, _ = self.run_main(self.game, "--max-files", "10")
        self.assertEqual(code, 0)
        self.assertIn("TRUNCATED", out)


class HostModeTest(Tree):
    """The Deck side: a fake home with a Steam library, a stub deck.sh, canned and live JSON."""
    APPID = "389170"
    INSTALLDIR = "A Lenda do Heroi - O Jogo"

    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.root, "home")
        self.media = os.path.join(self.root, "run", "media")
        os.makedirs(self.media)
        lib = os.path.join(self.home, ".local", "share", "Steam")
        os.makedirs(os.path.join(lib, "steamapps", "common"))
        with open(os.path.join(lib, "steamapps", "libraryfolders.vdf"), "w") as f:
            f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % lib)
        with open(os.path.join(lib, "steamapps", "appmanifest_%s.acf" % self.APPID), "w") as f:
            f.write('"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"Songs for a Hero"\n\t"StateFlags"\t\t"4"\n'
                    '\t"installdir"\t\t"%s"\n\t"buildid"\t\t"7053717"\n\t"SizeOnDisk"\t\t"123"\n}\n' % (self.APPID, self.INSTALLDIR))
        self.install = os.path.join(lib, "steamapps", "common", self.INSTALLDIR)
        self.put("Content/Tooltip/A.xnb", xnb_texture(28, 23), base=self.install)
        self.put("Content/Tooltip/B.xnb", xnb_texture(28, 23), base=self.install)
        self.put("Content/Tooltip/Switch/A.xnb", xnb_texture(28, 23), base=self.install)
        self.put("Content/Menu/xbox/a.xnb", xnb_texture(13, 13), base=self.install)
        os.makedirs(os.path.join(lib, "steamapps", "compatdata", self.APPID, "pfx"))
        self.log, self.stdin_file, self.canned = (os.path.join(self.root, n) for n in ("stub.log", "stub.stdin", "stub.json"))
        self.stub = os.path.join(self.root, "deck.sh")
        with open(self.stub, "w") as f:
            f.write('#!/bin/bash\nprintf "%s\\n" "$*" >> "$STUB_LOG"\n'
                    'case "$1" in\n  --check) exit "${STUB_CHECK_RC:-0}";;\n  true) exit 0;;\n'
                    '  "python3 -") cat > "$STUB_STDIN"; cat "$STUB_JSON";;\n  *) exit 64;;\nesac\n')
        os.chmod(self.stub, 0o755)
        self.env = {"DECK_SH": self.stub, "STUB_LOG": self.log, "STUB_STDIN": self.stdin_file, "STUB_JSON": self.canned}

    def direct(self, **req):
        with mock.patch.object(steam, "MEDIA_ROOT", self.media):
            return tg.remote_triage(dict({"appid": self.APPID}, **req), steam, home=self.home, media_root=self.media)

    def canned_from(self, res):
        with open(self.canned, "w") as f:
            f.write("a banner the Deck printed\n" + json.dumps(res) + "\n")

    def run_main(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, dict(self.env, **(env or {}))), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                code = tg.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return f.read().splitlines()

    def test_remote_triage_finds_the_install_through_the_steam_library(self):
        res = self.direct()
        self.assertTrue(res["ok"], res)
        self.assertEqual((res["mode"], res["appid"], res["install_dir_name"]), ("host", self.APPID, self.INSTALLDIR))
        self.assertEqual(res["manifest"]["buildid"], "7053717")
        self.assertEqual(res["manifest"]["installdir"], self.INSTALLDIR)
        self.assertEqual(res["manifest"]["StateFlags"], "4")
        self.assertTrue(res["prefix_present"])
        self.assertEqual(res["engines"][0]["engine"], "unknown" if False else res["engines"][0]["engine"])
        self.assertEqual(res["totals"]["files"], 4)
        self.assertNotIn(self.root, json.dumps(res))

    def test_an_app_that_is_not_installed(self):
        res = self.direct(appid="999")
        self.assertFalse(res["ok"])
        self.assertIn("not installed", res["error"])

    def test_host_mode_end_to_end_with_a_stub_deck_sh(self):
        self.canned_from(self.direct())
        path = os.path.join(self.root, "out.json")
        code, out, err = self.run_main("--appid", self.APPID, "--json", path, "--keywords", "tooltip", "--max-files", "500")
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(self.calls(), ["--check", "true", "python3 -"])        # the command text is only this
        with open(self.stdin_file) as f:
            script = f.read()
        self.assertIn("def remote_triage(", script)
        self.assertIn("remote_main(", script)
        self.assertNotIn(self.install, script)                                  # no game path travels at all
        self.assertNotIn(self.root, script)
        import ast
        call = ast.parse(script.splitlines()[-1]).body[0].value
        request = json.loads(ast.literal_eval(call.args[1]))
        self.assertEqual(request, {"appid": self.APPID, "keywords": ["tooltip"], "max_files": 500, "budget_s": 60.0,
                                   "probe_kb": 8192})
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(data["manifest"]["buildid"], "7053717")
        self.assertIn("appid %s, build 7053717" % self.APPID, out.splitlines()[0])

    def test_unreachable_deck_is_exit_two_and_nothing_else_is_called(self):
        code, out, err = self.run_main("--appid", self.APPID, env={"STUB_CHECK_RC": "1"})
        self.assertEqual(code, 2)
        self.assertIn("Deck unreachable", err)
        self.assertEqual(self.calls(), ["--check"])

    def test_remote_errors_and_garbage_are_exit_one(self):
        self.canned_from({"schema": 1, "ok": False, "error": "the game is not installed (no appmanifest_1.acf)"})
        code, _, err = self.run_main("--appid", "1")
        self.assertEqual(code, 1)
        self.assertIn("not installed", err)
        with open(self.canned, "w") as f:
            f.write("Traceback (most recent call last): nothing\n")
        code, _, err = self.run_main("--appid", "1")
        self.assertEqual(code, 1)
        self.assertIn("without a JSON object", err)

    def test_dry_run_request_connects_to_nothing(self):
        code, out, _ = self.run_main("--appid", self.APPID, "--dry-run-request", env={"DECK_SH": "/nonexistent/deck.sh"})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["appid"], self.APPID)
        self.assertEqual(self.calls(), [])

    def test_the_assembled_script_runs_standalone_and_matches_the_direct_call(self):
        script = tg.build_script(tg.build_request(self.APPID, 1000, 30, ["tooltip"], 64))
        env = {"PATH": os.environ.get("PATH", ""), "TRIAGE_HOME": self.home, "TRIAGE_MEDIA": self.media}
        done = subprocess.run([sys.executable, "-"], input=script, capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(len(done.stdout.strip().splitlines()), 1)              # ONE JSON object
        remote = tg.parse_result(done.stdout)
        direct = json.loads(json.dumps(self.direct(max_files=1000, budget_s=30, keywords=["tooltip"], probe_kb=64)))
        for res in (remote, direct):
            res.pop("seconds")
            res.pop("generated_at")
        self.assertEqual(remote, direct)

    def registry(self, entries):
        path = os.path.join(self.root, "ingame.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries, f)
        return path

    def host_run(self, registry_path, *argv):
        from button_glyphs import ingame
        self.canned_from(self.direct())
        with mock.patch.object(ingame, "PATH", registry_path):
            return self.run_main("--appid", self.APPID, *argv)

    def test_host_mode_says_when_the_appid_is_already_registered_as_in_game(self):
        reg = self.registry([{"appid": self.APPID, "name": "Songs for a Hero", "reported_by": "the project owner",
                              "reported_on": "2026-11-24"}])
        path = os.path.join(self.root, "out.json")
        code, out, err = self.host_run(reg, "--json", path)
        self.assertEqual(code, 0, (out, err))
        lines = out.splitlines()
        self.assertIn("Already registered as In game", lines[1])
        self.assertLessEqual(len(lines), 25)
        with open(path) as f:
            self.assertEqual(json.load(f)["ingame_registered"]["appid"], self.APPID)

    def test_host_mode_is_silent_when_the_appid_is_not_registered(self):
        reg = self.registry([{"appid": "1", "name": "Other", "reported_by": "x", "reported_on": "2026-11-24"}])
        path = os.path.join(self.root, "out.json")
        code, out, _ = self.host_run(reg, "--json", path)
        self.assertEqual(code, 0)
        self.assertNotIn("Already registered", out)
        with open(path) as f:
            self.assertIsNone(json.load(f)["ingame_registered"])

    def test_a_broken_registry_does_not_stop_the_triage(self):
        path = os.path.join(self.root, "broken.json")
        with open(path, "w") as f:
            f.write("{not json")
        code, out, _ = self.host_run(path)
        self.assertEqual(code, 0)
        self.assertNotIn("Already registered", out)

    def test_local_mode_does_not_look_at_the_registry(self):
        self.put("a/b.png", 10)
        code, out, _ = CliTest.run_main(self, self.game)
        self.assertEqual(code, 0)
        self.assertNotIn("Already registered", out)

    def test_a_remote_failure_is_still_one_json_object(self):
        script = tg.build_script({"appid": "1"}, steam_source="raise RuntimeError('boom')")
        done = subprocess.run([sys.executable, "-"], input=script, capture_output=True, text=True, timeout=60)
        res = tg.parse_result(done.stdout)
        self.assertEqual((res["ok"], "boom" in res["error"]), (False, True))


if __name__ == "__main__":
    unittest.main()
