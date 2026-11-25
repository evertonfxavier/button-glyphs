"""A synthetic Serious Sam HD style game folder (HD .tex textures: RGBA8 ovals, DXT5 prompts, a pad, a CStaticAtlas .rsc).

Layout like the real one: `Content/SeriousSamHD_X/All.gro` (the textures and the atlas) plus other archives, a
`Content/SeriousSamHD_X/CachedShaders_PC.gro`, an exe. The atlas holds two 64x64 prompt cells in its first block
(128x64 DXT5) and a second, bigger block that must never change. No game bytes.
"""
import os
import random
import zlib

import croteam_fixture as F
import overlay_fixture as of

from button_glyphs import gro, image, overlay_patch
from button_glyphs.profiles.overlay_glyphs import AtlasCells, OverlayGlyphProfile, RoundSymbol, WholePicture
from button_glyphs.profiles.region_glyphs import PictureLoader

APPID = "41010"
INSTALLDIR = "Serious Sam HD The Second Encounter"
MOUNT = "Content/SeriousSamHD_X"
PREFIX = "Content/SeriousSamHD/Textures/"
OVAL = PREFIX + "Menu/A_button.tex"
DISC = PREFIX + "Menu/XBoxButtonA.tex"
SHOULDER = PREFIX + "Menu/XBoxShoulderButtonL.tex"
PAD = PREFIX + "Menu/GamepadPerspective.tex"
ATLAS = PREFIX + "GlobalAtlas.rsc"
OVAL_BOX, DISC_BOX, SHOULDER_BOX = (5, 4, 25, 28), (14, 14, 50, 50), (4, 13, 59, 51)
CELLS = {"disc": (64, 0), "shoulder": (0, 0)}


def sha(data):
    return of.sha(data)


def disc_picture(seed):
    """A 64x64 RGBA: a white disc (transparent corners) bleeding white, like the real prompts."""
    px = bytearray()
    for y in range(64):
        for x in range(64):
            d = ((x - 31.5) ** 2 + (y - 31.5) ** 2) ** 0.5
            px += bytes((255, 251, 255, 255 if d < 18 else 0))
    return bytes(px)


def oval_tex(seed=0):
    rnd = random.Random(seed)
    px = bytearray()
    for y in range(32):
        for x in range(32):
            inside = ((x - 15) / 10.0) ** 2 + ((y - 16) / 12.0) ** 2 <= 1
            px += bytes((rnd.randrange(256), 200, 20, 255)) if inside else bytes((41, 157, 20, 0))
    px = bytes(px)
    return F.build_hd(32, 32, 5, F.hd_rgba_block(F.rgba_chain(px, 32, 32)), stamp=b"$Version: Synthetic HD; X")


def dxt_tex(w, h, seed):
    stored, _ = F.flat_dxt(w, h, fmt=10, seed=seed)
    return F.build_hd(w, h, 10, stored, stamp=b"$Version: Synthetic HD; X")


def atlas(seed=0):
    first, _ = F.flat_dxt(128, 64, fmt=10, seed=seed)
    second, _ = F.flat_dxt(128, 128, fmt=10, seed=seed + 4)
    data, _ = F.build_atlas([(128, 64, 10, first), (128, 128, 10, second)], stamp=b"$Version: Synthetic HD; atlas")
    return data


def textures(variant=0):
    return {OVAL: oval_tex(variant), DISC: dxt_tex(64, 64, 1 + variant), SHOULDER: dxt_tex(64, 64, 2 + variant),
            PAD: dxt_tex(64, 64, 3 + variant), ATLAS: atlas(variant)}


def build_game(install, variant=0, extra_archives=()):
    overlay_patch.clear_caches()
    mount = os.path.join(install, *MOUNT.split("/"))
    os.makedirs(mount, exist_ok=True)
    base = [(p, b"older version of %s" % p.encode()) for p in textures()]
    files = {
        "All.gro": gro.build(list(textures(variant).items()) + [("Content/x/junk.bin", of.junk(200_000, 7))], deflate=True),
        "All_9999.gro": gro.build([("Content/x/patch.bin", b"patch")], deflate=True),
        "CachedShaders_PC.gro": gro.build([("Content/x/shader.bin", of.junk(5000, 8))], deflate=True),
    }
    for name, entries in extra_archives:
        files[name] = gro.build(entries, deflate=True)
    for name, data in files.items():
        with open(os.path.join(mount, name), "wb") as f:
            f.write(data)
    with open(os.path.join(install, "SamHD_TSE.exe"), "wb") as f:
        f.write(of.junk(4000, 9))
    del base


def make_art(art_dir, shift=0):
    for i, name in enumerate(("a-icon", "l-icon")):
        of.icon_png(os.path.join(art_dir, "glyphs", "ps", name + ".png"), i + shift)
    pad = bytearray()
    for y in range(64):
        for x in range(64):
            pad += bytes((x * 4, y * 4, 128 + shift * 20, 255 if 8 <= y < 56 else 0))
    os.makedirs(os.path.join(art_dir, "masks"), exist_ok=True)
    image.write_png(os.path.join(art_dir, "masks", "pad.png"), bytes(pad), 64, 64)


def entries_for(install):
    out = []
    paths = list(textures())
    gros = [os.path.join(install, *MOUNT.split("/"), n) for n in sorted(os.listdir(os.path.join(install, *MOUNT.split("/"))))
            if n.endswith(".gro")]
    keys = {OVAL: "A_button", DISC: "XBoxButtonA", SHOULDER: "XBoxShoulderButtonL", PAD: "GamepadPerspective",
            ATLAS: "GlobalAtlas"}
    dims = {OVAL: (32, 32, "rgba8"), DISC: (64, 64, "dxt5"), SHOULDER: (64, 64, "dxt5"), PAD: (64, 64, "dxt5"),
            ATLAS: (128, 64, "dxt5")}
    with gro.Layers(gros) as layers:
        for path in paths:
            _, data, winner = layers.read(path)
            out.append({"key": keys[path], "path": path, "size": len(data), "sha256": sha(data),
                        "crc32": "%08x" % (zlib.crc32(data) & 0xFFFFFFFF), "gro": os.path.basename(winner),
                        "width": dims[path][0], "height": dims[path][1], "fmt": dims[path][2]})
    return out


def make_profile(entries, art_dir, vehicle="gro", revision=1, **kw):
    disc = RoundSymbol("glyphs/ps/a-icon", DISC_BOX, "bleed")
    shoulder = RoundSymbol("glyphs/ps/l-icon", SHOULDER_BOX, "bleed", None, "box")
    builders = {
        "A_button": RoundSymbol("glyphs/ps/a-icon", OVAL_BOX, "bleed"),
        "XBoxButtonA": disc,
        "XBoxShoulderButtonL": shoulder,
        "GamepadPerspective": WholePicture("masks/pad", (27, 26, 27)),
        "GlobalAtlas": AtlasCells(0, [(CELLS["disc"] + (64, 64, disc)), (CELLS["shoulder"] + (64, 64, shoulder))]),
    }
    args = dict(appid=APPID, name="Fixture HD", entries=entries, builders=builders, vehicle=vehicle,
                roles=[("a", "A")], native_style="xbox", revision=revision, mount=MOUNT, loose_root=".",
                archive="zz_ButtonGlyphs.gro", pictures=PictureLoader(art_dir))
    args.update(kw)
    return OverlayGlyphProfile(**args)
