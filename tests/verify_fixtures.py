"""Synthetic games for tests/test_verify_profile.py: one small fixture per strategy kind.

Each `*_fixture(base)` writes the user's "copies" (what tools/verify_profile.py is given as <dir>) under
`base` and returns (src_dir, extra) ; each `*_profiles(arg)` returns {appid: profile} and is deterministic, so
the crash probe's child process can rebuild the very same profile from `module:function` and `arg`.
No game data is used anywhere.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from button_glyphs import image, xnb  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402
from button_glyphs.profiles.region_glyphs import PictureLoader, RegionGlyphProfile  # noqa: E402
from button_glyphs.profiles.xnb_pictures import XnbPictureProfile  # noqa: E402
import forge_fixture as fx  # noqa: E402
import test_files_patch as tfp  # noqa: E402
import test_regions as trg  # noqa: E402
from test_backend import fake_charlie_exe  # noqa: E402
from test_entries import AREA, AREA_ENTRY, PRIMARY, make_profile as entries_profile  # noqa: E402
from test_ten_second_ninja_x import DATA_WIN, SAVE_REL, padded, save_text, test_profile as ninja_profile  # noqa: E402

FILES_APPID, REGIONS_APPID, ENTRIES_APPID, IL_APPID, CONFIG_APPID = "389170", "474960", "15100", "405290", "435790"


# ---- loose files
def files_entries():
    out = []
    for key, w, h in tfp.DRAWN:
        data = tfp.original_xnb(key, w, h)
        tex = xnb.parse(data)
        out.append({"key": key, "file": "Content/%s.xnb" % key, "width": w, "height": h,
                    "header": tex.data_offset, "size": len(data), "sha256": tfp.sha(data),
                    "image": "t/" + key.replace("/", "_")})
    return out


def files_profiles(art_dir):
    return {FILES_APPID: XnbPictureProfile(
        appid=FILES_APPID, name="Songs for a Hero - Definitive Edition", content_dir="Content",
        files=files_entries(), roles=[("a", "A / X")], native_style="xbox", revision=1,
        pictures=PictureLoader(art_dir))}


def files_fixture(base):
    base = str(base)
    src, art = os.path.join(base, "src"), os.path.join(base, "art")
    os.makedirs(os.path.join(art, "t"))
    for key, w, h in tfp.DRAWN:
        _write(os.path.join(src, "Content", *(key + ".xnb").split("/")), tfp.original_xnb(key, w, h))
        image.write_png(os.path.join(art, "t", key.replace("/", "_") + ".png"), tfp.art_rgba(w, h), w, h)
    for key, w, h in tfp.UNTOUCHED:
        _write(os.path.join(src, "Content", *(key + ".xnb").split("/")), tfp.original_xnb(key, w, h))
    _write(os.path.join(src, "Content", "Other", "readme.txt"), b"not a texture")
    return src, art


# ---- regions
def regions_profiles(arg=None):
    textures = [trg.texture(7), trg.texture(9)]
    regions, _ = trg.make_regions(textures)
    return {REGIONS_APPID: RegionGlyphProfile(
        appid=REGIONS_APPID, name="Quantum Break", file=trg.ARCHIVE, regions=regions,
        roles=[("a", "A / X")], native_style="xbox", revision=1)}


def regions_fixture(base):
    src = os.path.join(str(base), "regions")
    for region, tex in zip(regions_profiles()[REGIONS_APPID].regions, [trg.texture(7), trg.texture(9)]):
        _write(os.path.join(src, region["key"].replace("/", "_") + ".bin"), tex)
    return src


# ---- entries (forge)
def entries_profiles(arg=None):
    return {ENTRIES_APPID: entries_profile()}


def entries_fixture(base):
    src = os.path.join(str(base), "forge")
    os.makedirs(src)
    fx.build_forge(os.path.join(src, PRIMARY))
    fx.build_forge(os.path.join(src, AREA), entry=AREA_ENTRY)
    return src


# ---- IL patch (the real Charlie Murder profile over a stand-in executable)
def il_profiles(arg=None):
    return {IL_APPID: PROFILES[IL_APPID]}


def il_fixture(base):
    src = os.path.join(str(base), "il")
    os.makedirs(src)
    fake_charlie_exe(os.path.join(src, "CharlieMurder.exe"))
    return src


# ---- config value
def config_profiles(arg=None):
    return {CONFIG_APPID: ninja_profile()}


def config_fixture(base, value="525.000000"):
    src = os.path.join(str(base), "config")
    _write(os.path.join(src, "data.win"), DATA_WIN)
    _write(os.path.join(src, SAVE_REL), padded(save_text(value)))
    return src


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
