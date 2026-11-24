#!/usr/bin/env python3
"""Dev tool: analyse Quantum Break's prompt atlases and generate the profile data.

Needs Pillow + numpy (the plugin backend itself is stdlib only), a directory holding the
original textures extracted from data/ep999-000.rmdp, and the Montserrat Bold font (OFL).

    python3 tools/qb_build.py <textures-dir> <Montserrat-Bold.ttf>

It writes
  py_modules/button_glyphs/masks/*.png                 white-on-transparent symbol / label masks
  py_modules/button_glyphs/profiles/quantum_break_cells.py   cell rectangles + original hashes

The textures are 32-bit BGRA with a 128-byte DDS header and no mipmaps, in four HUD scales
(75 %, 100 %, 150 %, 200 %) that share one layout. Every button cell is found by connected
components, classified by its position in the 100 % layout, and analysed once here so that
the runtime only has to fill, clear and blit.
"""
import hashlib
import os
import sys
from collections import Counter, deque

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASK_DIR = os.path.join(ROOT, "py_modules", "button_glyphs", "masks")
OUT_PY = os.path.join(ROOT, "py_modules", "button_glyphs", "profiles", "quantum_break_cells.py")

# data/textures/hardcoded/... inside ep999-000.rmdp: file name -> (offset, scale)
TEXTURES = {
    "prompts": {
        1.0: ("textures__hardcoded__prompts.tex", 34744949248),
        0.75: ("textures__hardcoded__hud75__prompts.tex", 34723647552),
        1.5: ("textures__hardcoded__hud150__prompts.tex", 34521919712),
        2.0: ("textures__hardcoded__hud200__prompts.tex", 34632835264),
    },
    "videoplayer_controls": {
        1.0: ("textures__hardcoded__videoplayer_controls.tex", 34763190592),
        0.75: ("textures__hardcoded__hud75__videoplayer_controls.tex", 34732211392),
        1.5: ("textures__hardcoded__hud150__videoplayer_controls.tex", 34556140928),
        2.0: ("textures__hardcoded__hud200__videoplayer_controls.tex", 34693664096),
    },
}
SCALE_TAG = {0.75: "hud75", 1.0: "hud100", 1.5: "hud150", 2.0: "hud200"}


# --------------------------------------------------------------------------- masks

def render_masks(font_path):
    os.makedirs(MASK_DIR, exist_ok=True)
    big, out = 1024, 256
    sw = 112          # stroke at 1024 px

    def finish(img, name):
        img = img.resize((out, out), Image.LANCZOS)
        white = Image.new("RGBA", img.size, (255, 255, 255, 0))
        white.putalpha(img.getchannel("A") if img.mode == "RGBA" else img)
        white.save(os.path.join(MASK_DIR, name + ".png"))

    def canvas():
        im = Image.new("L", (big, big), 0)
        return im, ImageDraw.Draw(im)

    im, d = canvas()                                   # cross
    a, b = 150, big - 150
    for p, q in (((a, a), (b, b)), ((a, b), (b, a))):
        d.line([p, q], fill=255, width=sw)
        for x, y in (p, q):
            d.ellipse([x - sw // 2, y - sw // 2, x + sw // 2, y + sw // 2], fill=255)
    finish(im, "cross")

    im, d = canvas()                                   # circle
    d.ellipse([90, 90, big - 90, big - 90], outline=255, width=sw)
    finish(im, "circle")

    im, d = canvas()                                   # square
    d.rounded_rectangle([140, 140, big - 140, big - 140], radius=30, outline=255, width=sw)
    finish(im, "square")

    im, d = canvas()                                   # triangle
    pts = [(512, 120), (big - 100, big - 200), (100, big - 200)]
    d.line(pts + [pts[0], pts[1]], fill=255, width=sw, joint="curve")
    finish(im, "triangle")

    font = ImageFont.truetype(font_path, 700)
    for text in ("L1", "R1", "L2", "R2"):
        im = Image.new("L", (2400, 1000), 0)
        ImageDraw.Draw(im).text((100, 100), text, fill=255, font=font)
        box = im.getbbox()
        im = im.crop(box)
        scale = 256 / max(im.size)
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                       Image.LANCZOS)
        white = Image.new("RGBA", im.size, (255, 255, 255, 0))
        white.putalpha(im)
        white.save(os.path.join(MASK_DIR, text + ".png"))


# --------------------------------------------------------------------------- analysis

def load_tex(path):
    d = open(path, "rb").read()
    assert d[:4] == b"DDS "
    h = int.from_bytes(d[12:16], "little")
    w = int.from_bytes(d[16:20], "little")
    assert len(d) == 128 + w * h * 4, path
    assert d[0x20:0x4C] == bytes(44), "DDS reserved area is not zero in " + path
    arr = np.frombuffer(d, dtype=np.uint8, offset=128).reshape(h, w, 4)
    return d, w, h, arr


def components(arr, dil):
    m = arr[..., 3] > 8
    dm = m.copy()
    for dy in range(-dil, dil + 1):
        for dx in range(-dil, dil + 1):
            dm |= np.roll(np.roll(m, dy, 0), dx, 1)
    h, w = dm.shape
    lab = np.zeros((h, w), np.int32)
    out = []
    ys, xs = np.nonzero(dm)
    n = 0
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if lab[y0, x0]:
            continue
        n += 1
        q = deque([(y0, x0)])
        lab[y0, x0] = n
        x_lo = x_hi = x0
        y_lo = y_hi = y0
        while q:
            y, x = q.popleft()
            x_lo, x_hi, y_lo, y_hi = min(x_lo, x), max(x_hi, x), min(y_lo, y), max(y_hi, y)
            for yy, xx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= yy < h and 0 <= xx < w and dm[yy, xx] and not lab[yy, xx]:
                    lab[yy, xx] = n
                    q.append((yy, xx))
        # tight box of the real (undilated) pixels
        sub = m[y_lo:y_hi + 1, x_lo:x_hi + 1]
        yy, xx = np.nonzero(sub)
        if len(yy):
            out.append((x_lo + int(xx.min()), y_lo + int(yy.min()),
                        x_lo + int(xx.max()), y_lo + int(yy.max())))
    return out


def classify_prompts(box, s):
    """Role of a component of the prompts atlas, from its (tight) box in the 100 % layout."""
    x0, y0, x1, y1 = (v / s for v in box)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    left, right = cx < 390, cx > 770
    centre = 640 < cx < 770
    col = (x0 % 128) > 40
    # face buttons: round discs (42 px black/yellow, 32 px steel, 28 px small yellow)
    if 27 <= w <= 44 and abs(w - h) <= 3 and (
            (cy < 125 and (left or right)) or (centre and 455 < cy < 575)):
        row = cy > 510 if centre else y0 > 40
        return "disc", ("x" if col else "y") if row else ("b" if col else "a")
    # bare letters A B / Y X (black, then coloured) in the centre column
    if centre and 11 <= w <= 22 and 12 <= h <= 21 and 255 < cy < 380 and cx > 700:
        lcol = x0 > 727
        row = int((cy - 255) // 32) % 2 == 1
        return "letter", ("x" if lcol else "y") if row else ("b" if lcol else "a")
    # triggers: tall trapezoids (49x55, steel 40x45, small 28x32)
    if 26 <= w <= 52 and 30 <= h <= 58 and 0.7 <= w / h <= 1.0 and (
            (left or right) and 125 < cy < 195 or centre and 580 < cy < 635):
        return "trig", "lt" if (x0 % 128) < 40 else "rt"
    # bumpers: wide trapezoids (75x36, steel 65x32, small 54x24)
    if w >= 50 and 20 <= h <= 40 and (
            (left or right) and (200 < cy < 250 or 262 < cy < 312)
            or centre and (650 < cy < 695 or 715 < cy < 760)):
        return "bump", "lb" if (cy < 258 or 650 < cy < 695) else "rb"
    return None


def classify_video(box, s):
    x0, y0, x1, y1 = (v / s for v in box)
    w, h = x1 - x0 + 1, y1 - y0 + 1
    if 30 <= w <= 44 and 30 <= h <= 44 and 320 < x0 < 345 and y0 < 40:
        return "disc", "b"
    return None


def mode_color(px):
    c = Counter(map(tuple, px.reshape(-1, 4).tolist()))
    return c.most_common(1)[0][0]


def chroma(c):
    return max(c[:3]) - min(c[:3])


def analyse(arr, box, kind, s):
    """Fill colour, text box and text style of one cell."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0 + 1, y1 - y0 + 1
    cell = arr[y0:y1 + 1, x0:x1 + 1].astype(np.int32)
    cx, cy = w / 2, h / 2
    yy, xx = np.mgrid[0:h, 0:w]
    # inside = columns between the first and last opaque pixel of every row (so glyph holes
    # count, the rim does not), kept clear of the edge by a margin
    solid = cell[..., 3] > 200
    m = max(2, int(round(2.5 * s)))
    inside = np.zeros((h, w), bool)
    for y in range(h):
        cols = np.nonzero(solid[y])[0]
        if len(cols) > 2 * m:
            inside[y, cols[0] + m:cols[-1] - m + 1] = True
    rows = np.nonzero(solid.any(1))[0]
    inside[:rows[0] + m] = False
    inside[rows[-1] - m + 1:] = False
    if kind == "disc":
        r = min(w, h) / 2
        inside &= ((xx + .5 - cx) ** 2 + (yy + .5 - cy) ** 2) <= (0.80 * r) ** 2
    elif kind == "letter":
        inside = np.ones((h, w), bool)
    if kind == "letter":
        fill = None
        txt_mask = cell[..., 3] > 40
        text_px = cell[txt_mask]
        mode_rgb = mode_color(arr[y0:y1 + 1, x0:x1 + 1][txt_mask]) if txt_mask.any() else (0, 0, 0, 255)
        text_box = (x0, y0, x1, y1)
        style = "ps" if chroma(mode_rgb) > 60 else "solid"
        color = (0, 0, 0) if style == "ps" else tuple(int(v) for v in mode_rgb[:3])
        return dict(fill=None, box=text_box, style=style, color=color)

    # shape fill = most common opaque colour of the cell
    opaque = arr[y0:y1 + 1, x0:x1 + 1][cell[..., 3] > 250]
    fill = mode_color(opaque)[:3]
    fa = np.array(fill, np.int32)
    dist = np.abs(cell[..., :3] - fa).sum(-1)
    differs = ((dist > 60) | (cell[..., 3] < 200)) & inside
    # drop stray pixels (edge halo) - keep the biggest cluster by row/column support
    if not differs.any():
        raise RuntimeError("no glyph found in cell %r" % (box,))
    ys, xs = np.nonzero(differs)
    tb = (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()), y0 + int(ys.max()))
    holes = (cell[..., 3] < 60) & differs
    if holes.sum() > 0.6 * differs.sum():
        style, color = "hole", (0, 0, 0)
    else:
        sub = arr[y0:y1 + 1, x0:x1 + 1][differs & (cell[..., 3] > 200)]
        if len(sub) == 0:
            return dict(fill=tuple(int(v) for v in fill), box=tb, style="hole", color=(0, 0, 0))
        far = sub[np.argsort(-np.abs(sub[:, :3].astype(np.int32) - fa).sum(-1))[:max(1, len(sub) // 4)]]
        mean = tuple(int(v) for v in far[:, :3].mean(0))
        if chroma(mean) > 60:
            style, color = "ps", (0, 0, 0)
        else:
            style, color = "solid", mean
    return dict(fill=tuple(int(v) for v in fill), box=tb, style=style, color=color)


def build_cells(name, arr, s):
    dil = 1 if s < 1 else int(round(2 * s))
    comps = components(arr, dil)
    classify = classify_prompts if name == "prompts" else classify_video
    cells = []
    for box in comps:
        res = classify(box, s)
        if not res:
            continue
        kind, role = res
        info = analyse(arr, box, kind, s)
        pad = max(1, round(1.5 * s))
        tb = info["box"]
        if kind == "disc":
            # symbols sit in a square 52 % of the disc; clear a little more than the old letter
            side = max(tb[2] - tb[0] + 1, tb[3] - tb[1] + 1)
            ccx, ccy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            half = max(side / 2 + pad, 0.30 * (box[2] - box[0] + 1))
            clear = (int(ccx - half), int(ccy - half), int(ccx + half), int(ccy + half))
            sym = 0.54 * (box[2] - box[0] + 1)
            draw = (round(ccx - sym / 2), round(ccy - sym / 2), round(ccx + sym / 2) - 1,
                    round(ccy + sym / 2) - 1)
        elif kind == "letter":
            clear = (tb[0] - 1, tb[1] - 1, tb[2] + 1, tb[3] + 1)
            side = max(tb[2] - tb[0] + 1, tb[3] - tb[1] + 1) + 2
            ccx, ccy = (tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2
            draw = (round(ccx - side / 2), round(ccy - side / 2), round(ccx + side / 2) - 1,
                    round(ccy + side / 2) - 1)
        else:  # trapezoid label
            clear = (tb[0] - pad, tb[1] - pad, tb[2] + pad, tb[3] + pad)
            draw = tb
        cells.append(dict(kind=kind, role=role, rect=tuple(int(v) for v in box),
                          clear=tuple(int(v) for v in clear), draw=tuple(int(v) for v in draw),
                          fill=info["fill"], style=info["style"], color=info["color"]))
    cells.sort(key=lambda c: (c["rect"][1] // 8, c["rect"][0]))
    return cells


def main():
    tex_dir, font = sys.argv[1], sys.argv[2]
    render_masks(font)
    lines = ['"""Generated by tools/qb_build.py from the original textures - do not edit.',
             '',
             'Per texture: offset inside data/ep999-000.rmdp, size, SHA-256 of the original bytes',
             'and the button cells (rect = whole shape, clear = area wiped, draw = where the',
             'new symbol/label goes; colours are B,G,R like the pixels). Rects are in texture pixels."""',
             '', 'TEXTURES = [']
    counts = {}
    for name, scales in TEXTURES.items():
        for s, (fname, offset) in sorted(scales.items()):
            data, w, h, arr = load_tex(os.path.join(tex_dir, fname))
            cells = build_cells(name, arr, s)
            counts.setdefault(name, {})[s] = Counter(c["kind"] + ":" + c["role"] for c in cells)
            print(name, SCALE_TAG[s], w, h, len(cells), dict(Counter(c["kind"] for c in cells)))
            lines += ['    {', '        "key": "%s/%s",' % (name, SCALE_TAG[s]),
                      '        "offset": %d, "size": %d, "width": %d, "height": %d,' % (offset, len(data), w, h),
                      '        "sha256": "%s",' % hashlib.sha256(data).hexdigest(),
                      '        "cells": [']
            for c in cells:
                lines.append("            %r," % (c,))
            lines += ['        ],', '    },']
    lines.append(']')
    with open(OUT_PY, "w") as f:
        f.write("\n".join(lines) + "\n")
    # every scale must expose the same cells
    for name, per in counts.items():
        ref = per[1.0]
        for s, cnt in per.items():
            if cnt != ref:
                print("WARNING: %s %s differs from 100%%: %s" % (name, SCALE_TAG[s], dict((ref - cnt) + (cnt - ref))))
    print("wrote", OUT_PY)


if __name__ == "__main__":
    main()
