#!/usr/bin/env python3
"""Dev tool: prepare the DualSense picture that replaces Quantum Break's `controller.tex`.

Needs Pillow + numpy (the plugin backend itself is stdlib only), the user's drawing (line art on
a white background) and the original `textures/menu/controller.tex` extracted from
data/ep999-000.rmdp (only read: its size, header, hash and footprint end up in the table; the
texture itself is never copied).

    python3 tools/qb_controller_art.py <dualsense.png> <controller.tex> [<preview-dir>]

It writes
  py_modules/button_glyphs/masks/controller_dualsense.png     RGBA drawing, cut out and scaled
  py_modules/button_glyphs/profiles/quantum_break_pictures.py  region entry (offset, size, SHA-256,
                                                               box, colour of transparent pixels)

Cut-out: the white background is found by flood fill from the border (the body interior stays
opaque white, as in the game's own picture). Along the outer contour the alpha comes from the
distance to white, so the anti-aliased edge blends into the dark menu instead of leaving a halo.
The drawing is scaled in premultiplied space to TARGET_W pixels wide, which is what the final
placement needs, so the plugin never has to resample at run time.
"""
import hashlib
import os
import sys
from collections import Counter

import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASK_DIR = os.path.join(ROOT, "py_modules", "button_glyphs", "masks")
OUT_PNG = os.path.join(MASK_DIR, "controller_dualsense.png")
OUT_PY = os.path.join(ROOT, "py_modules", "button_glyphs", "profiles", "quantum_break_pictures.py")

IMAGE = "controller_dualsense"
OFFSET = 40082693152            # textures/menu/controller.tex inside ep999-000.rmdp (build 1412244)
KEY = "controller/menu"
TARGET_W = 704                  # width of the pad on the 1024 texture (the Xbox pad was 626)
BOX_H = 500                     # room above/below; the drawing is centred in it
BG_THRESH = 235                 # grey level from which a pixel may be background
HEADER = 128


def load_tex(path):
    with open(path, "rb") as f:
        d = f.read()
    assert d[:4] == b"DDS ", "not a DDS file"
    h = int.from_bytes(d[12:16], "little")
    w = int.from_bytes(d[16:20], "little")
    assert len(d) == HEADER + w * h * 4, "not raw 32-bit BGRA without mips"
    assert d[0x20:0x4C] == bytes(44), "DDS reserved area is not zero"
    arr = np.frombuffer(d, dtype=np.uint8, offset=HEADER).reshape(h, w, 4)
    return d, w, h, arr


def exterior_mask(gray):
    """Pixels of the background: light pixels connected to the border."""
    h, w = gray.shape
    cand = np.zeros((h + 2, w + 2), np.uint8)
    cand[1:-1, 1:-1] = (gray >= BG_THRESH) * 255
    cand[0, :] = cand[-1, :] = cand[:, 0] = cand[:, -1] = 255
    img = Image.fromarray(cand).copy()                  # fromarray shares the buffer: not writable
    ImageDraw.floodfill(img, (0, 0), 128)               # 4-connected
    return (np.array(img) == 128)[1:-1, 1:-1]


def dilate(m, n):
    out = m.copy()
    for _ in range(n):
        p = np.pad(out, 1)
        out = p[1:-1, 1:-1] | p[:-2, 1:-1] | p[2:, 1:-1] | p[1:-1, :-2] | p[1:-1, 2:]
    return out


def cut_out(rgb):
    """RGB float array (paper white ~250) -> straight RGBA uint8 with a clean outer edge."""
    paper = np.median(rgb[:4, :].reshape(-1, 3), axis=0)           # the background colour
    rgb = np.clip(rgb * (255.0 / paper), 0, 255)                   # paper -> pure white
    gray = rgb.mean(-1)
    ext = exterior_mask(gray)
    band = dilate(ext, 2) & ~ext                                   # the anti-aliased rim
    # colour of the contour line: the dark pixels of the rim
    dark = band & (gray < 120)
    line = np.median(rgb[dark], axis=0)
    line_gray = float(line.mean())
    alpha = np.ones(gray.shape, np.float64)
    alpha[ext] = 0.0
    cover = np.clip((255.0 - gray) / (255.0 - line_gray), 0.0, 1.0)
    alpha[band] = cover[band]
    # un-mix the rim: pixel = line * a + white * (1 - a)
    out_rgb = rgb.copy()
    a3 = np.maximum(alpha, 1e-6)[..., None]
    unmixed = np.clip((rgb - 255.0 * (1 - a3)) / a3, 0, 255)
    sel = band & (alpha > 0)
    out_rgb[sel] = unmixed[sel]
    out = np.zeros(gray.shape + (4,), np.uint8)
    out[..., :3] = np.rint(out_rgb).astype(np.uint8)
    out[..., 3] = np.rint(alpha * 255).astype(np.uint8)
    return out, line, ext


def scale_premultiplied(rgba, width):
    im = Image.fromarray(rgba).convert("RGBa")
    h = round(im.height * width / im.width)
    return im.resize((width, h), Image.LANCZOS).convert("RGBA")


def convention(arr):
    """Straight alpha; the colour the game stores under fully transparent pixels (B, G, R)."""
    clear = arr[arr[..., 3] == 0][:, :3]
    colours = Counter(map(tuple, clear.tolist()))
    (bgr, n), = colours.most_common(1)
    return tuple(int(v) for v in bgr), n / len(clear)


def main():
    art, tex = sys.argv[1], sys.argv[2]
    prev = sys.argv[3] if len(sys.argv) > 3 else None
    data, w, h, arr = load_tex(tex)
    assert (w, h) == (1024, 1024)
    clear_bgr, share = convention(arr)
    ys, xs = np.nonzero(arr[..., 3] > 8)
    foot = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    cx, cy = (foot[0] + foot[2] + 1) // 2, (foot[1] + foot[3] + 1) // 2

    rgb = np.asarray(Image.open(art).convert("RGB"), dtype=np.float64)
    rgba, line, ext = cut_out(rgb)
    ys, xs = np.nonzero(rgba[..., 3] > 8)
    crop = rgba[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    pic = scale_premultiplied(crop, TARGET_W)
    os.makedirs(MASK_DIR, exist_ok=True)
    pic.save(OUT_PNG, optimize=True)
    pw, ph = pic.size
    assert ph <= BOX_H
    box = (cx - TARGET_W // 2, cy - BOX_H // 2, cx - TARGET_W // 2 + TARGET_W - 1,
           cy - BOX_H // 2 + BOX_H - 1)
    print("footprint of the original pad:", foot, "centre", (cx, cy))
    print("line colour", line.round(), "| bundled", OUT_PNG, pic.size, os.path.getsize(OUT_PNG), "bytes")
    print("transparent pixels in the game: BGR", clear_bgr, "%.4f of them" % share)
    print("box", box)

    lines = ['"""Generated by tools/qb_controller_art.py - do not edit.',
             '',
             'Quantum Break pictures that are replaced as a whole (no cells): the PlayStation style',
             'swaps the game\'s Xbox pad drawing for the bundled DualSense drawing. Per region: offset',
             'inside data/ep999-000.rmdp, size, SHA-256 of the original bytes, the drawing',
             '(masks/<image>.png) with the box it is centred in (inclusive pixel rectangle on the',
             'texture) and the colour (B, G, R) the game keeps under fully transparent pixels."""',
             '', 'PICTURES = [', '    {', '        "key": "%s",' % KEY,
             '        "offset": %d, "size": %d, "width": %d, "height": %d,' % (OFFSET, len(data), w, h),
             '        "sha256": "%s",' % hashlib.sha256(data).hexdigest(),
             '        "cells": [],',
             '        "replace": {"image": "%s", "box": %r, "clear": %r},' % (IMAGE, box, clear_bgr),
             '    },', ']']
    with open(OUT_PY, "w") as f:
        f.write("\n".join(lines) + "\n")
    print("wrote", OUT_PY)

    if prev:                                    # visual check on the dark menu colour
        os.makedirs(prev, exist_ok=True)
        bg = Image.new("RGBA", (w, h), (40, 40, 48, 255))
        bg.alpha_composite(pic, (box[0], box[1] + (BOX_H - ph) // 2))
        bg.convert("RGB").save(os.path.join(prev, "controller_art_preview.png"))
        mask = Image.fromarray(np.where(ext, 0, 255).astype(np.uint8))
        mask.save(os.path.join(prev, "controller_art_exterior.png"))


if __name__ == "__main__":
    main()
