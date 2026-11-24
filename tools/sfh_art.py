#!/usr/bin/env python3
"""Dev tool: turn the user's PlayStation pixel-art sheet into native-size PNGs for Songs for a Hero.

Songs for a Hero - Definitive Edition (Steam 389170) draws its prompts from tiny textures (13x13 to
28x23 px). The profile replaces 24 of them by a whole-texture picture (`replace`, like the Quantum
Break DualSense). This tool is the bridge from the user's own redrawn sheet (39 icons, about 8 screen
px per art pixel, transparent background) to those files. Needs Pillow + numpy (dev only; the plugin
backend is stdlib only). Run it from button-glyphs/ with plain `python3` (not -I, Pillow must be found):

    python3 tools/sfh_art.py <sheet.png> [--content <Content-dir>] [--out <review-dir>] [--no-install]

  <sheet.png>   the user's sheet (RGBA). It is NEVER copied into the repo.
  --content     a copy of the game's Content folder: only read, for the review sheets (original vs
                candidate). Nothing from it is written anywhere.
  --out         where the review sheets and manifest.json go (outside the repo).
  --no-install  do not write py_modules/button_glyphs/masks/sfh/*.png and
                py_modules/button_glyphs/profiles/songs_for_a_hero_art.py.

Steps (all deterministic, no randomness):
  1. SLICE   pixels with alpha < 64 are dropped (the near-black fringe and specks live there); the rest
             is labelled into 8-connected components (own labeller, no scipy). The 39 biggest are the icon
             bodies; the small ones (D-pad rays, stick arrows) join the nearest body. Rows are found from
             the vertical gaps between centres, columns by x, and the names come from ICON_NAMES in
             reading order. The count and the row layout are checked: anything else stops the run.
  2. FIT     each target texture has a footprint = union of the `rect`s of its cells in the generated
             table songs_for_a_hero_cells.py (the area the original art occupies). The icon is scaled to
             fit it (aspect kept; size nudged by at most one pixel so that it can be centred exactly)
             and centred; the rest of the texture is transparent (0,0,0,0).
  3. SCALE   area (box) filter over the icon's own pixels, weighted by how much each colour differs from
             the icon's body colour (SALIENCY_K): at 0.5-0.8x a 1-pixel symbol line would otherwise be
             averaged into the dark body and turn muddy; the weighting keeps the symbol colour while
             outlines and shading still blend. Alpha is the plain area coverage of the kept pixels
             (premultiplied-correct: no dark halo), snapped to 0/255 at 50 %.
  4. CLEAN   alpha-isolated single pixels are removed, one-pixel holes inside the art are filled.
The game's own art has no soft edges and no mip levels, hence the hard alpha.
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_MODULES = os.path.join(ROOT, "py_modules")
sys.path.insert(0, PY_MODULES)
sys.path.insert(0, os.path.join(PY_MODULES, "button_glyphs", "profiles"))
from button_glyphs import xnb  # noqa: E402  (only used to read the originals for the review sheets)

MASK_DIR = os.path.join(PY_MODULES, "button_glyphs", "masks", "sfh")
ART_PY = os.path.join(PY_MODULES, "button_glyphs", "profiles", "songs_for_a_hero_art.py")
CELLS_PY = os.path.join(PY_MODULES, "button_glyphs", "profiles", "songs_for_a_hero_cells.py")

# Reading order of the user's sheet: 6 icons per row, 7 rows, the last row has 3.
ICON_NAMES = [
    "Triangle", "Circle", "Cross", "Square", "Dpad", "DpadUp",
    "DpadDown", "DpadLeft", "DpadRight", "Hold", "LeftStickDown", "LeftStickLeft",
    "LeftStickNeutral", "LeftStickRight", "LeftStickUp", "L1", "R1", "L2",
    "R2", "Options", "Touchpad", "PsButton", "L3", "R3",
    "Share", "OptionsText", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "L1Text", "R1Text", "L2Text", "R2Text", "L3Text", "R3Text",
    "TouchpadText", "PsText", "OptionsAlt",
]
ROW_COUNTS = (6, 6, 6, 6, 6, 6, 3)

ALPHA_MIN = 64            # sheet pixels below this alpha are fringe/specks and are dropped
SNAP = 0.5                # output alpha: coverage >= SNAP -> 255, else 0
SALIENCY_K = 3.0          # weight of a colour far from the body colour (1 + K at distance >= SAL_DIST)
SAL_DIST = 128.0
PALETTE_MAX = 12          # dominant colours kept per icon (mode "class")
PALETTE_DIST = 40.0       # minimum RGB distance between two palette colours
PALETTE_MIN_SHARE = 0.002 # a colour needs this share of the icon's pixels
GLYPH_CHROMA = 70         # a pixel is "glyph" (symbol, text, lit part) when max-min of RGB >= this ...
GLYPH_LUMA = 200          # ... or when it is nearly white
GLYPH_GROW = 6            # sheet px around a glyph that are not body either (its dark fringe)
GLYPH_SNAP = 0.45         # glyph pixel when its box cell is covered this much (strokes are thin: below SNAP)
# Hand-set label font for variant B (our own 3x5 drawing): the text of a bumper/trigger icon is too small to
# survive scaling (a 1-pixel stroke at 0.7x), so B redraws just the label in this font on top of the scaled body.
FONT_3X5 = {
    "L": ("x..", "x..", "x..", "x..", "xxx"),
    "R": ("xx.", "x.x", "xx.", "x.x", "x.x"),
    "1": (".x.", "xx.", ".x.", ".x.", "xxx"),
    "2": ("xx.", "..x", ".x.", "x..", "xxx"),
}
MAX_SATELLITE_GAP = 80    # px on the sheet between a ray/arrow and the body it belongs to

# Which sheet icon fills which texture (the user's decisions); the order is the order of the sheets.
TARGETS = [
    ("Tooltip/A", "Cross"), ("Tooltip/B", "Circle"), ("Tooltip/X", "Square"), ("Tooltip/Y", "Triangle"),
    ("Tooltip/Dpad", "Dpad"), ("Tooltip/DpadUp", "DpadUp"), ("Tooltip/DpadDown", "DpadDown"),
    ("Tooltip/DpadLeft", "DpadLeft"), ("Tooltip/DpadRight", "DpadRight"),
    ("Tooltip/SHOULDER_LEFT", "L1"), ("Tooltip/SHOULDER_RIGHT", "R1"),
    ("Tooltip/TRIGGER_LEFT", "L2"), ("Tooltip/TRIGGER_RIGHT", "R2"),
    ("Tooltip/START", "Options"),
    ("Menu/xbox/a", "Cross"), ("Menu/xbox/b", "Circle"), ("Menu/xbox/x", "Square"), ("Menu/xbox/y", "Triangle"),
    ("Menu/xbox/lbumper", "L1Text"), ("Menu/xbox/rbumper", "R1Text"),
    ("Menu/xbox/ltrigger", "L2Text"), ("Menu/xbox/rtrigger", "R2Text"),
    ("Menu/xbox/start", "Options"), ("Menu/xbox/select", "Share"),
]


class SheetError(ValueError):
    """The sheet does not have the expected 39 icons / layout."""


# --------------------------------------------------------------------------- labelling and slicing

def label_components(mask, connectivity=8):
    """(labels, count): connected components of a bool mask, labels 1..count, 0 = background.

    Row-run union-find (no scipy). connectivity 8 joins diagonal neighbours, 4 does not."""
    mask = np.asarray(mask, dtype=bool)
    h, w = mask.shape
    reach = 1 if connectivity == 8 else 0
    parent = [0]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    padded = np.zeros((h, w + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    runs_by_row = []
    prev = []
    for y in range(h):
        d = np.diff(padded[y])
        starts = np.flatnonzero(d == 1).tolist()
        ends = (np.flatnonzero(d == -1) - 1).tolist()            # inclusive
        cur = []
        j = 0
        for s, e in zip(starts, ends):
            lab = 0
            while j < len(prev) and prev[j][1] < s - reach:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] <= e + reach:
                other = prev[k][2]
                if lab == 0:
                    lab = other
                else:
                    ra, rb = find(lab), find(other)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
                k += 1
            if lab == 0:
                lab = len(parent)
                parent.append(lab)
            cur.append((s, e, lab))
        runs_by_row.append(cur)
        prev = cur
    ids = {}
    labels = np.zeros((h, w), dtype=np.int32)
    for y, cur in enumerate(runs_by_row):
        for s, e, lab in cur:
            root = find(lab)
            if root not in ids:
                ids[root] = len(ids) + 1
            labels[y, s:e + 1] = ids[root]
    return labels, len(ids)


def component_stats(labels, count):
    """(areas, boxes): areas[i] pixels and boxes[i] = (x0, y0, x1, y1) inclusive, index 0 unused."""
    ys, xs = np.nonzero(labels)
    lab = labels[ys, xs]
    areas = np.bincount(lab, minlength=count + 1)
    big = np.iinfo(np.int64).max
    x0 = np.full(count + 1, big, dtype=np.int64)
    y0 = x0.copy()
    x1 = np.full(count + 1, -1, dtype=np.int64)
    y1 = x1.copy()
    np.minimum.at(x0, lab, xs)
    np.minimum.at(y0, lab, ys)
    np.maximum.at(x1, lab, xs)
    np.maximum.at(y1, lab, ys)
    boxes = np.stack([x0, y0, x1, y1], axis=1)
    return areas, boxes


def _box_gap(a, b):
    dx = max(int(a[0]) - int(b[2]), int(b[0]) - int(a[2]), 0)
    dy = max(int(a[1]) - int(b[3]), int(b[1]) - int(a[3]), 0)
    return max(dx, dy)


class Icon(object):
    """One slice of the sheet: `rgb` (h,w,3 uint8), `mask` (h,w bool, kept pixels), `box` on the sheet."""

    def __init__(self, name, rgb, mask, box, parts):
        self.name = name
        self.rgb = rgb
        self.mask = mask
        self.box = box            # (x0, y0, x1, y1) inclusive, sheet coordinates
        self.parts = parts        # number of connected components that make the icon

    @property
    def size(self):
        return self.mask.shape[1], self.mask.shape[0]


def slice_sheet(rgba, names=None, row_counts=None, alpha_min=ALPHA_MIN):
    """Cut the sheet into len(names) icons, named in reading order; raises SheetError if it does not fit."""
    names = list(ICON_NAMES if names is None else names)
    row_counts = tuple(ROW_COUNTS if row_counts is None else row_counts)
    if sum(row_counts) != len(names):
        raise ValueError("row_counts %r do not add up to %d names" % (row_counts, len(names)))
    rgba = np.asarray(rgba)
    mask = rgba[..., 3] >= alpha_min
    labels, count = label_components(mask)
    if count < len(names):
        raise SheetError("only %d connected pieces on the sheet, expected at least %d icons"
                         % (count, len(names)))
    areas, boxes = component_stats(labels, count)
    order = sorted(range(1, count + 1), key=lambda i: (-int(areas[i]), i))
    bodies, satellites = order[:len(names)], order[len(names):]
    # the 39 bodies must be clearly bigger than the biggest ray/arrow, or the count is a coincidence
    if satellites and areas[bodies[-1]] < 1.5 * areas[satellites[0]]:
        raise SheetError("cannot tell %d icon bodies from their small parts: the %dth piece has %d px "
                         "and the next %d px" % (len(names), len(names), areas[bodies[-1]],
                                                 areas[satellites[0]]))
    members = {b: [b] for b in bodies}
    for s in satellites:
        gaps = sorted((_box_gap(boxes[s], boxes[b]), b) for b in bodies)
        if gaps[0][0] > MAX_SATELLITE_GAP:
            raise SheetError("a small piece at %s is %d px away from the nearest icon: stray speck "
                             "above the alpha threshold?" % (tuple(boxes[s]), gaps[0][0]))
        members[gaps[0][1]].append(s)
    groups = []
    for b in bodies:
        gb = np.array([boxes[i] for i in members[b]])
        box = (int(gb[:, 0].min()), int(gb[:, 1].min()), int(gb[:, 2].max()), int(gb[:, 3].max()))
        groups.append({"labels": members[b], "box": box,
                       "cx": (box[0] + box[2]) / 2.0, "cy": (box[1] + box[3]) / 2.0})
    groups.sort(key=lambda g: (g["cy"], g["cx"]))
    rows, pos = [], 0
    for n in row_counts:
        rows.append(groups[pos:pos + n])
        pos += n
    for i in range(len(rows) - 1):
        boundary = rows[i + 1][0]["cy"] - rows[i][-1]["cy"]
        inner = max([r[k + 1]["cy"] - r[k]["cy"] for r in (rows[i], rows[i + 1]) for k in range(len(r) - 1)]
                    or [0])
        if boundary <= inner:
            raise SheetError("rows %d and %d are not separated (centre gap %.0f px, widest gap inside a "
                             "row %.0f px): the icons are not laid out as %r"
                             % (i + 1, i + 2, boundary, inner, row_counts))
    ordered = [g for r in rows for g in sorted(r, key=lambda g: g["cx"])]
    icons = []
    for name, g in zip(names, ordered):
        x0, y0, x1, y1 = g["box"]
        sel = np.isin(labels[y0:y1 + 1, x0:x1 + 1], g["labels"])
        icons.append(Icon(name, rgba[y0:y1 + 1, x0:x1 + 1, :3].copy(), sel, g["box"], len(g["labels"])))
    return icons


# --------------------------------------------------------------------------- scaling and fitting

def _axis_weights(n_src, n_dst):
    """(n_dst, n_src) area weights: row i holds the share of every source cell inside dst cell i."""
    edges = np.arange(n_dst + 1, dtype=np.float64) * n_src / n_dst
    w = np.zeros((n_dst, n_src))
    for i in range(n_dst):
        lo, hi = edges[i], edges[i + 1]
        for j in range(int(lo), min(int(np.ceil(hi)), n_src)):
            w[i, j] = max(0.0, min(hi, j + 1) - max(lo, j))
    return w / w.sum(axis=1, keepdims=True)


def area_resize(a, out_h, out_w):
    """Box/area resize of a (h,w) or (h,w,c) float array to (out_h, out_w[, c])."""
    a = np.asarray(a, dtype=np.float64)
    wy = _axis_weights(a.shape[0], out_h)
    wx = _axis_weights(a.shape[1], out_w)
    if a.ndim == 2:
        return wy @ a @ wx.T
    return np.stack([wy @ a[..., c] @ wx.T for c in range(a.shape[2])], axis=2)


def body_colour(rgb, mask):
    """The colour covering most of the kept pixels (float RGB): the biggest dominant-palette entry."""
    px = rgb[mask].astype(np.float64)
    if len(px) == 0:
        return np.zeros(3)
    pal = dominant_palette(rgb, mask)
    counts = np.bincount(_nearest(px, pal), minlength=len(pal))
    return pal[counts.argmax()]


def dominant_palette(rgb, mask, max_colours=PALETTE_MAX, min_dist=PALETTE_DIST, min_share=PALETTE_MIN_SHARE):
    """The icon's dominant colours (float RGB, most common first) from the kept pixels.

    Greedy over 5-bit colour bins by population; a bin joins when it is at least `min_dist` (RGB
    distance) from every colour already chosen and holds at least `min_share` of the pixels. Pixels are
    then assigned to the nearest entry and each entry becomes the mean of its pixels."""
    px = rgb[mask].astype(np.float64)
    if len(px) == 0:
        return np.zeros((1, 3))
    q = px.astype(np.int64)
    keys = (q[:, 0] >> 3) * 1024 + (q[:, 1] >> 3) * 32 + (q[:, 2] >> 3)
    counts = np.bincount(keys, minlength=32768)
    sums = np.stack([np.bincount(keys, weights=px[:, c], minlength=32768) for c in range(3)], axis=1)
    palette = []
    for b in np.argsort(-counts, kind="stable"):
        if counts[b] < min_share * len(px) or len(palette) >= max_colours:
            break
        c = sums[b] / counts[b]
        if all(np.sqrt(((c - p) ** 2).sum()) >= min_dist for p in palette):
            palette.append(c)
    if not palette:
        palette.append(px.mean(axis=0))
    pal = np.array(palette)
    for _ in range(2):                                            # settle the entries on their pixels
        idx = _nearest(px, pal)
        for i in range(len(pal)):
            if (idx == i).any():
                pal[i] = px[idx == i].mean(axis=0)
    return pal


def _nearest(px, palette):
    d = ((px[:, None, :] - palette[None, :, :]) ** 2).sum(axis=2)
    return d.argmin(axis=1)


def downscale(rgb, mask, out_w, out_h, k=SALIENCY_K, snap=SNAP, mode="class"):
    """rgba uint8 (out_h, out_w, 4): the kept pixels of an icon scaled to out_w x out_h, hard alpha.

    mode "class" (pixel-art friendly): the icon is reduced to its dominant colours, every output pixel
    takes the colour with the biggest area share x weight, the weight being 1 + k for colours far from
    the body colour (symbols, text, outlines) and 1 for the body. Flat palette colours stay flat and a
    1-pixel symbol line keeps its colour instead of dissolving into the dark body.
    mode "mean": the same weights, but the colour is the weighted mean (continuous, muddier)."""
    m = mask.astype(np.float64)
    body = body_colour(rgb, mask)
    cover = area_resize(m, out_h, out_w)
    out = np.zeros((out_h, out_w, 4), dtype=np.uint8)
    opaque = cover >= snap
    if mode == "mean":
        dist = np.sqrt(((rgb.astype(np.float64) - body) ** 2).sum(axis=2))
        w = (1.0 + k * np.minimum(1.0, dist / SAL_DIST)) * m
        num = area_resize(rgb.astype(np.float64) * w[..., None], out_h, out_w)
        den = area_resize(w, out_h, out_w)
        col = np.where(den[..., None] > 0, num / np.maximum(den[..., None], 1e-12), 0.0)
    else:
        pal = dominant_palette(rgb, mask)
        cls = np.full(mask.shape, -1, dtype=np.int64)
        cls[mask] = _nearest(rgb[mask].astype(np.float64), pal)
        weight = 1.0 + k * np.minimum(1.0, np.sqrt(((pal - body) ** 2).sum(axis=1)) / SAL_DIST)
        score = np.stack([area_resize((cls == i).astype(np.float64), out_h, out_w) * weight[i]
                          for i in range(len(pal))], axis=2)
        col = pal[score.argmax(axis=2)]
    out[..., :3] = np.where(opaque[..., None], np.clip(np.rint(col), 0, 255), 0).astype(np.uint8)
    out[..., 3] = np.where(opaque, 255, 0).astype(np.uint8)
    return out


def glyph_mask(rgb, mask):
    """Pixels of the symbol / text / lit part: strongly coloured or nearly white (bool, within mask)."""
    px = rgb.astype(np.int64)
    chroma = px.max(axis=2) - px.min(axis=2)
    luma = (px[..., 0] * 299 + px[..., 1] * 587 + px[..., 2] * 114) // 1000
    return mask & ((chroma >= GLYPH_CHROMA) | (luma >= GLYPH_LUMA))


def _dilate(m, r):
    out = m.copy()
    p = np.pad(m, r)
    h, w = m.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= p[r + dy:r + dy + h, r + dx:r + dx + w]
    return out


def _fill_holes(m):
    """m plus every background region that does not touch the border (4-connected)."""
    labels, count = label_components(~m, connectivity=4)
    if count == 0:
        return m
    edge = set(labels[0].tolist()) | set(labels[-1].tolist()) | set(labels[:, 0].tolist()) | set(labels[:, -1].tolist())
    holes = ~np.isin(labels, sorted(edge | {0})) & ~m
    return m | holes


def set_label(text):
    """Bool bitmap (5 rows) of `text` set in FONT_3X5 with a 1-pixel gap between the letters."""
    cols = []
    for i, ch in enumerate(text):
        rows = FONT_3X5[ch]
        bitmap = np.array([[c == "x" for c in r] for r in rows], dtype=bool)
        cols.append(bitmap)
        if i < len(text) - 1:
            cols.append(np.zeros((5, 1), dtype=bool))
    return np.concatenate(cols, axis=1)


def downscale_layers(rgb, mask, out_w, out_h, k=SALIENCY_K, snap=SNAP, label=None):
    """Two layers: the body (outline, shading) and the glyph (symbol, text, lit part) are scaled apart.

    The glyph is cut out of the body (its fringe too, the hole it leaves is filled with the body
    colour) and the body is scaled as in mode "class". The glyph's own bounding box is then mapped on
    a whole-pixel box of the output, so a symbol's outer strokes sit exactly on pixel rows/columns
    (a 1-art-pixel line at 0.7x would otherwise straddle two output pixels and vanish), and is drawn on
    top in its own dominant colours with a slightly lower coverage threshold (GLYPH_SNAP) because its
    strokes are thin. Without a glyph (a plain pad) this is exactly mode "class"."""
    g = glyph_mask(rgb, mask)
    if not g.any():
        return downscale(rgb, mask, out_w, out_h, k=k, snap=snap, mode="class")
    body_colour_rgb = body_colour(rgb, mask & ~_dilate(g, GLYPH_GROW))
    cut = _dilate(g, GLYPH_GROW)
    body_mask = _fill_holes(mask & ~cut)
    body_rgb = rgb.copy()
    body_rgb[cut | (body_mask & ~mask)] = np.rint(body_colour_rgb).astype(np.uint8)
    out = downscale(body_rgb, body_mask, out_w, out_h, k=k, snap=snap, mode="class")
    h, w = mask.shape
    ys, xs = np.nonzero(g)
    gx0, gx1, gy0, gy1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    sx, sy = out_w / float(w), out_h / float(h)
    ox0, ox1 = int(round(gx0 * sx)), int(round(gx1 * sx))
    oy0, oy1 = int(round(gy0 * sy)), int(round(gy1 * sy))
    ox1, oy1 = min(out_w, max(ox1, ox0 + 1)), min(out_h, max(oy1, oy0 + 1))
    ox0, oy0 = min(ox0, ox1 - 1), min(oy0, oy1 - 1)
    crop = (slice(gy0, gy1), slice(gx0, gx1))
    pal = dominant_palette(rgb[crop], g[crop], max_colours=3, min_dist=60.0, min_share=0.02)
    if label:
        text = set_label(label)
        th, tw = text.shape
        x = int(round((ox0 + ox1) / 2.0 - tw / 2.0))
        y = int(round((oy0 + oy1) / 2.0 - th / 2.0))
        x, y = max(0, min(x, out_w - tw)), max(0, min(y, out_h - th))
        region = out[y:y + th, x:x + tw]
        region[text, :3] = np.clip(np.rint(pal[0]), 0, 255).astype(np.uint8)
        region[text, 3] = 255
        return out
    gm = g[crop].astype(np.float64)
    cover = area_resize(gm, oy1 - oy0, ox1 - ox0)
    cls = np.full(gm.shape, -1, dtype=np.int64)
    cls[g[crop]] = _nearest(rgb[crop][g[crop]].astype(np.float64), pal)
    score = np.stack([area_resize((cls == i).astype(np.float64), oy1 - oy0, ox1 - ox0) for i in range(len(pal))],
                     axis=2)
    colour = np.clip(np.rint(pal[score.argmax(axis=2)]), 0, 255).astype(np.uint8)
    on = cover >= GLYPH_SNAP
    region = out[oy0:oy1, ox0:ox1]
    region[on, :3] = colour[on]
    region[on, 3] = 255
    return out


def snap_alpha(rgba, threshold=128):
    """Alpha to 0/255 at `threshold`; colour of transparent pixels forced to 0 (the game's convention)."""
    out = np.array(rgba, dtype=np.uint8, copy=True)
    opaque = out[..., 3] >= threshold
    out[..., 3] = np.where(opaque, 255, 0)
    out[~opaque, :3] = 0
    return out


def _neighbours(opaque, four=False):
    """Per pixel: number of opaque 8-neighbours (or 4-neighbours)."""
    p = np.pad(opaque.astype(np.int32), 1)
    h, w = opaque.shape
    shifts = [(-1, 0), (1, 0), (0, -1), (0, 1)]
    if not four:
        shifts += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    return sum(p[1 + dy:1 + dy + h, 1 + dx:1 + dx + w] for dy, dx in shifts)


def clean(rgba):
    """Remove alpha-isolated single pixels; fill one-pixel holes that have 4 opaque neighbours."""
    out = np.array(rgba, dtype=np.uint8, copy=True)
    opaque = out[..., 3] > 0
    if opaque.sum() > 1:
        lonely = opaque & (_neighbours(opaque) == 0)
        out[lonely] = 0
    opaque = out[..., 3] > 0
    holes = (~opaque) & (_neighbours(opaque, four=True) == 4)
    for y, x in zip(*np.nonzero(holes)):
        nb = [out[y + dy, x + dx, :3] for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1))]
        out[y, x, :3] = np.rint(np.mean(nb, axis=0)).astype(np.uint8)
        out[y, x, 3] = 255
    return out


def fit_size(iw, ih, fw, fh):
    """Pixel size (w, h) of an iw x ih icon fitted into an fw x fh footprint (aspect kept).

    The exact fit is rounded, but a size that lets the icon sit exactly centred (same parity as the
    footprint) wins when it costs less than about half a pixel of stretch."""
    s = min(fw / float(iw), fh / float(ih))
    ex, ey = iw * s, ih * s
    best = None
    for a in range(max(1, int(ex) - 1), min(fw, int(ex) + 2) + 1):
        for b in range(max(1, int(ey) - 1), min(fh, int(ey) + 2) + 1):
            cost = (a - ex) ** 2 + (b - ey) ** 2 + 0.3 * (((fw - a) % 2) + ((fh - b) % 2))
            if best is None or cost < best[0] - 1e-9:
                best = (cost, a, b)
    return best[1], best[2]


def place(icon_rgba, tex_w, tex_h, footprint):
    """A tex_w x tex_h transparent canvas with the icon centred in the footprint (x0, y0, x1, y1)."""
    fx0, fy0, fx1, fy1 = footprint
    ih, iw = icon_rgba.shape[:2]
    x = fx0 + (fx1 - fx0 + 1 - iw) // 2
    y = fy0 + (fy1 - fy0 + 1 - ih) // 2
    if x < 0 or y < 0 or x + iw > tex_w or y + ih > tex_h:
        raise ValueError("icon %dx%d at (%d,%d) leaves the %dx%d texture" % (iw, ih, x, y, tex_w, tex_h))
    canvas = np.zeros((tex_h, tex_w, 4), dtype=np.uint8)
    canvas[y:y + ih, x:x + iw] = icon_rgba
    return canvas, (x, y, x + iw - 1, y + ih - 1)


def render_texture(icon, tex_w, tex_h, footprint, k=SALIENCY_K, mode="layers", label=None):
    """The native-size RGBA texture (tex_h, tex_w, 4) for an Icon, plus the rectangle it occupies."""
    fw = footprint[2] - footprint[0] + 1
    fh = footprint[3] - footprint[1] + 1
    iw, ih = icon.size
    ow, oh = fit_size(iw, ih, fw, fh)
    if mode == "layers":
        small = downscale_layers(icon.rgb, icon.mask, ow, oh, k=k, label=label)
    else:
        small = downscale(icon.rgb, icon.mask, ow, oh, k=k, mode=mode)
    small = clean(snap_alpha(small))
    return place(small, tex_w, tex_h, footprint)


def union_rect(cells):
    """Union of the inclusive `rect`s of a texture's cells = where the original art lives."""
    rects = [c["rect"] for c in cells]
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


def load_table(path=CELLS_PY):
    """{key: entry} of the generated cell table (entries are plain dicts)."""
    ns = {}
    with open(path, "r", encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"), ns)       # a generated data file: literals only
    return {e["key"]: e for e in ns["TEXTURES"]}


# --------------------------------------------------------------------------- review sheets (outside the repo)

DARK = (40, 40, 48)
LIGHT = (230, 230, 235)


def read_original(content, key):
    """(h,w,4) uint8 pixels of Content/<key>.xnb (read only)."""
    with open(os.path.join(content, key + ".xnb"), "rb") as f:
        data = f.read()
    tex = xnb.parse(data)
    return np.frombuffer(xnb.pixels(data), dtype=np.uint8).reshape(tex.height, tex.width, 4).copy()


def on_background(rgba, bg, scale, footprint=None):
    """PIL RGB image: the texture composited on `bg`, nearest-neighbour upscaled, footprint outlined."""
    h, w = rgba.shape[:2]
    base = Image.new("RGBA", (w, h), bg + (255,))
    base.alpha_composite(Image.fromarray(np.ascontiguousarray(rgba)))
    big = base.resize((w * scale, h * scale), Image.NEAREST).convert("RGB")
    if footprint is not None:
        x0, y0, x1, y1 = footprint
        ImageDraw.Draw(big).rectangle([x0 * scale - 1, y0 * scale - 1, (x1 + 1) * scale, (y1 + 1) * scale],
                                      outline=(255, 0, 255) if bg == DARK else (200, 0, 160))
    return big


def _font(size):
    return ImageFont.load_default(size=size)


def slice_sheet_image(icons):
    """Labelled contact sheet of the 39 slices (name check)."""
    cell_w, cell_h, cols = 230, 200, 6
    rows = (len(icons) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (120, 120, 128))
    draw = ImageDraw.Draw(sheet)
    for i, ic in enumerate(icons):
        r, c = divmod(i, cols)
        rgba = np.zeros(ic.mask.shape + (4,), dtype=np.uint8)
        rgba[..., :3] = ic.rgb
        rgba[..., 3] = np.where(ic.mask, 255, 0)
        im = Image.fromarray(rgba)
        s = min(200.0 / im.width, 150.0 / im.height, 1.0)
        im = im.resize((max(1, round(im.width * s)), max(1, round(im.height * s))), Image.LANCZOS)
        sheet.paste(im, (c * cell_w + (cell_w - im.width) // 2, r * cell_h + 32 + (150 - im.height) // 2), im)
        draw.text((c * cell_w + 6, r * cell_h + 5), "%d %s (%dx%d)" % (i + 1, ic.name, ic.size[0], ic.size[1]),
                  fill=(255, 255, 0), font=_font(16))
    return sheet


def compare_sheet(items, bg, scale=10, columns=2):
    """items: [(label, original|None, [(variant, rgba), ...], footprint)] -> PIL image.

    Each cell shows the original, then every candidate variant, at `scale`x on `bg`."""
    text = (255, 255, 255) if bg == DARK else (20, 20, 30)
    tiles = []
    for label, orig, variants, fp in items:
        parts = []
        if orig is not None:
            parts.append(("original", on_background(orig, bg, scale)))
        for vname, rgba in variants:
            parts.append(("candidate " + vname, on_background(rgba, bg, scale, fp)))
        tiles.append((label, parts))
    pad, head = 14, 24
    tile_w = [sum(p.width for _, p in parts) + pad * (len(parts) - 1) for _, parts in tiles]
    tile_h = [max(p.height for _, p in parts) + head for _, parts in tiles]
    col_w = max(tile_w)
    rows = [tiles[i:i + columns] for i in range(0, len(tiles), columns)]
    row_h = [max(tile_h[i * columns + j] for j in range(len(r))) for i, r in enumerate(rows)]
    sheet = Image.new("RGB", (columns * (col_w + 30) + 20, sum(h + 22 for h in row_h) + 20), bg)
    draw = ImageDraw.Draw(sheet)
    y = 10
    for i, r in enumerate(rows):
        for j, (label, parts) in enumerate(r):
            x = 20 + j * (col_w + 30)
            draw.text((x, y), label, fill=text, font=_font(15))
            for name, p in parts:
                sheet.paste(p, (x, y + head))
                draw.text((x + 2, y + head + p.height + 1), name, fill=text, font=_font(11))
                x += p.width + pad
        y += row_h[i] + 22
    return sheet


def native_strip(rows_data, scale):
    """rows_data: [(label, [rgba, ...])] -> PIL image: every texture side by side at `scale`x on dark and light."""
    gap = 6
    bands = []
    for bg in (DARK, LIGHT):
        for label, rgbas in rows_data:
            ims = [on_background(a, bg, scale) for a in rgbas]
            w = sum(im.width + gap * scale for im in ims)
            band = Image.new("RGB", (w, max(im.height for im in ims) + 4), bg)
            x = 0
            for im in ims:
                band.paste(im, (x, 2))
                x += im.width + gap * scale
            bands.append(band)
    sheet = Image.new("RGB", (max(b.width for b in bands), sum(b.height for b in bands)), DARK)
    y = 0
    for b in bands:
        sheet.paste(b, (0, y))
        y += b.height
    return sheet
