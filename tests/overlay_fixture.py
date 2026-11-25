"""A synthetic Croteam-style game folder for the add-file overlay strategy (Serious Sam 2 layout, no game bytes).

Root `.gro` archives (plain zips) in load order: All_PC_01 (old 8x8 versions of the textures, a 300 KB junk entry the
plugin must never read), Movies_PC_01 (junk), Patch_02_100_800000 (the 16x16 textures that WIN). Non-archive files sit
beside them (an exe, an ini) so a test can prove that nothing but our added file changes.
"""
import hashlib
import os
import random
import zlib

import croteam_fixture as F

from button_glyphs import gro, image, overlay_patch
from button_glyphs.profiles.overlay_glyphs import OverlayGlyphProfile, RoundSymbol
from button_glyphs.profiles.region_glyphs import PictureLoader

APPID = "204340"
INSTALLDIR = "Serious Sam 2 é"
PREFIX = "Content/SeriousSam2/Textures/Menu/"
WINNER = "Patch_02_100_800000.gro"
SIZE = 16
BOX = (3, 2, 13, 14)                       # 10 x 12: diameter 10
KEYS = [("A_button", (0, 0, 0), "a-icon"), ("B_button", (0, 0, 0), "b-icon"), ("White_button", (255, 255, 255), "w-icon")]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def texture(key, size=SIZE, variant=0):
    """A fake original .tex: a pseudo-random straight BGRA picture, with the game's transparent convention."""
    seed = sum(key.encode()) + variant * 17
    rnd = random.Random(seed)
    clear = dict((k, c) for k, c, _ in KEYS)[key]
    px = bytearray()
    for i in range(size * size):
        if rnd.random() < 0.4:
            px += bytes(clear) + b"\0"
        else:
            px += bytes((rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), 255))
    return F.build_se2(size, size, bytes(px))


def junk(n, seed):
    return random.Random(seed).randbytes(n)


def build_game(install, variant=0, extra_archives=()):
    """Write the fake game into `install`. `variant` changes the WINNING originals (a game update)."""
    overlay_patch.clear_caches()                 # a rewritten archive may keep its size and (coarse) timestamp
    os.makedirs(install, exist_ok=True)
    base = [(PREFIX + k + ".tex", texture(k, 8)) for k, _, _ in KEYS]
    base += [("Content/SeriousSam2/Other/junk.bin", junk(300_000, 1)), (PREFIX + "PC_Esc.tex", F.build_se2(8, 8, bytes(256)))]
    patch = [(PREFIX + k + ".tex", texture(k, SIZE, variant)) for k, _, _ in KEYS]
    patch += [(PREFIX + "Other.tex", F.build_se2(8, 8, bytes(range(256))))]
    files = {
        "All_PC_01.gro": gro.build(base, deflate=True),
        "Movies_PC_01.gro": gro.build([("Content/Movies/a.bin", junk(1000, 2))], deflate=True),
        WINNER: gro.build(patch, deflate=True),
        "Sam2.exe": junk(5000, 3),
    }
    for name, entries in extra_archives:
        files[name] = gro.build(entries, deflate=True)
    for name, data in files.items():
        with open(os.path.join(install, name), "wb") as f:
            f.write(data)
    os.makedirs(os.path.join(install, "Content", "SeriousSam2"), exist_ok=True)
    with open(os.path.join(install, "Content", "SeriousSam2", "Sam2.ini"), "wb") as f:
        f.write(b"fil_bPreferZips = 0\n")


def icon_png(path, seed):
    """A 12x12 RGBA icon: a ring in a seed colour with an asymmetric mark, transparent elsewhere."""
    rgba = bytearray()
    for y in range(12):
        for x in range(12):
            d = ((x - 5.5) ** 2 + (y - 5.5) ** 2) ** 0.5
            on = 4.0 <= d <= 5.8 or (x == 2 and y < 6)
            rgba += bytes((40 + seed * 50, 200 - seed * 40, 90 + seed * 30, 255)) if on else b"\0\0\0\0"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image.write_png(path, bytes(rgba), 12, 12)


def make_art(art_dir, shift=0):
    for i, (_, _, name) in enumerate(KEYS):
        icon_png(os.path.join(art_dir, "glyphs", "ps", name + ".png"), i + shift)


def entries_for(install):
    """The pins of the fixture's WINNING originals (read back from the files, like the real table's pins)."""
    out = []
    with gro.Layers([os.path.join(install, n) for n in sorted(os.listdir(install)) if n.endswith(".gro")]) as layers:
        for k, _, _ in KEYS:
            path = PREFIX + k + ".tex"
            _, data, winner = layers.read(path)
            out.append({"key": k, "path": path, "size": len(data), "sha256": sha(data),
                        "crc32": "%08x" % (zlib.crc32(data) & 0xFFFFFFFF), "gro": os.path.basename(winner),
                        "width": SIZE, "height": SIZE})
    return out


def make_profile(entries, art_dir, vehicle="gro", revision=1, appid=APPID, **kw):
    builders = {k: RoundSymbol("glyphs/ps/" + name, BOX, clear) for k, clear, name in KEYS}
    args = dict(appid=appid, name="Fixture Sam", entries=entries, builders=builders, vehicle=vehicle,
                roles=[("a", "A")], native_style="xbox", revision=revision, mount=".",
                archive="zz_ButtonGlyphs.gro", pictures=PictureLoader(art_dir))
    args.update(kw)
    return OverlayGlyphProfile(**args)
