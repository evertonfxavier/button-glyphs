#!/usr/bin/env python3
"""Dev tool: measure Songs for a Hero's controller prompts and generate the profile data.

Songs for a Hero - Definitive Edition (Steam 389170, MonoGame). The prompts are tiny
uncompressed XNB Texture2D files (85-byte header + raw RGBA, size == 85 + w*h*4). Needs
Pillow + numpy (the plugin backend itself is stdlib only) and a copy of the game's
Content folder. Re-derive the table (and the contact sheets) with:

    python3 tools/sfh_build.py <Content-dir> [--sheets <out-dir>]

It writes  py_modules/button_glyphs/profiles/songs_for_a_hero_cells.py  (hashes, offsets,
rectangles, colours; no pixels) and, with --sheets, contact sheets of the originals
(nearest-neighbour upscaled, dark background, boxes overlaid) which stay OUT of the repo.

How the boxes are measured (all from pixels, no hand-typed coordinates):
  * shape      = bounding box of the pixels with alpha > 0
  * rim colour = the colour most often touching a transparent pixel (the outline)
  * fill       = the most common remaining colour (not rim, not near-white)
  * face discs = glyph is what differs from the fill inside the shape eroded by 2 px (the rim
                 and its specular highlight stay out); the four siblings (A B X Y) share one
                 layout, so `clear` is the union of their glyph boxes, `draw` a centred square
  * labels     = connected components (8-neighbour) other than the biggest one (the shape);
                 Menu/xbox labels are the pure-white pixels
  * D-pad / stick "highlighted" state = the orange (255,186,0) pixels, split into
                 connected components (rays / pointer / ring / arrow)
Pixels are R,G,B,A in memory, rows top-down; a transparent pixel is (0,0,0,0) (checked).
"""
import hashlib
import os
import sys
from collections import Counter, deque

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
from button_glyphs import xnb  # noqa: E402

OUT_PY = os.path.join(ROOT, "py_modules", "button_glyphs", "profiles",
                      "songs_for_a_hero_cells.py")

ORANGE = (255, 186, 0)          # "lit" colour of the tooltip D-pad / stick variants
WHITE_MIN = 225                 # near-white = specular highlight / Menu label text

# key (relative to Content, no .xnb) -> (family, role, ps icon, ps action)
#   ps action: redraw = replace the symbol, relabel = replace the text label,
#              same = identical art on a PlayStation pad (recommend leaving),
#              optional = Xbox-360 look that a PS pad shows differently, harmless to keep
SPEC = [
    ("Tooltip/A", "tip_face", "a", "cross", "redraw"),
    ("Tooltip/B", "tip_face", "b", "circle", "redraw"),
    ("Tooltip/X", "tip_face", "x", "square", "redraw"),
    ("Tooltip/Y", "tip_face", "y", "triangle", "redraw"),
    ("Tooltip/Dpad", "tip_dpad", "dpad", "dpad", "optional"),
    ("Tooltip/DpadUp", "tip_dpad", "dpad_up", "dpad_up", "optional"),
    ("Tooltip/DpadDown", "tip_dpad", "dpad_down", "dpad_down", "optional"),
    ("Tooltip/DpadLeft", "tip_dpad", "dpad_left", "dpad_left", "optional"),
    ("Tooltip/DpadRight", "tip_dpad", "dpad_right", "dpad_right", "optional"),
    ("Tooltip/Hold", "tip_text", "hold", None, "same"),
    ("Tooltip/LEFT_STICK_DOWN", "tip_stick", "lstick_down", None, "same"),
    ("Tooltip/LEFT_STICK_LEFT", "tip_stick", "lstick_left", None, "same"),
    ("Tooltip/LEFT_STICK_NEUTRAL", "tip_stick", "lstick_neutral", None, "same"),
    ("Tooltip/LEFT_STICK_RIGHT", "tip_stick", "lstick_right", None, "same"),
    ("Tooltip/LEFT_STICK_UP", "tip_stick", "lstick_up", None, "same"),
    ("Tooltip/SHOULDER_LEFT", "tip_label", "lb", "L1", "relabel"),
    ("Tooltip/SHOULDER_RIGHT", "tip_label", "rb", "R1", "relabel"),
    ("Tooltip/TRIGGER_LEFT", "tip_label", "lt", "L2", "relabel"),
    ("Tooltip/TRIGGER_RIGHT", "tip_label", "rt", "R2", "relabel"),
    ("Tooltip/START", "tip_start", "start", "options", "redraw"),
    ("Menu/xbox/a", "menu_face", "a", "cross", "redraw"),
    ("Menu/xbox/b", "menu_face", "b", "circle", "redraw"),
    ("Menu/xbox/x", "menu_face", "x", "square", "redraw"),
    ("Menu/xbox/y", "menu_face", "y", "triangle", "redraw"),
    ("Menu/xbox/dup", "menu_arrow", "dpad_up", "dpad_up", "same"),
    ("Menu/xbox/ddown", "menu_arrow", "dpad_down", "dpad_down", "same"),
    ("Menu/xbox/dleft", "menu_arrow", "dpad_left", "dpad_left", "same"),
    ("Menu/xbox/dright", "menu_arrow", "dpad_right", "dpad_right", "same"),
    ("Menu/xbox/lbumper", "menu_label", "lb", "L1", "relabel"),
    ("Menu/xbox/rbumper", "menu_label", "rb", "R1", "relabel"),
    ("Menu/xbox/ltrigger", "menu_label", "lt", "L2", "relabel"),
    ("Menu/xbox/rtrigger", "menu_label", "rt", "R2", "relabel"),
    ("Menu/xbox/lstick", "menu_stick", "lstick", None, "same"),
    ("Menu/xbox/rstick", "menu_stick", "rstick", None, "same"),
    ("Menu/xbox/start", "menu_symbol", "start", "options", "redraw"),
    ("Menu/xbox/select", "menu_symbol", "select", "share", "redraw"),
]
# Present in the folder but NOT referenced by name in DumaLegend.exe (which loads
# "Menu/xbox/" + lstick|rstick|...): a probable typo duplicate of rstick. Measured anyway.
EXTRA = [
    ("Menu/xbox/rstrick", "menu_stick", "rstick", None, "same"),
]

LABEL_KIND = {"lb": "bump", "rb": "bump", "lt": "trig", "rt": "trig"}


# --------------------------------------------------------------------------- pixel helpers

def load(content, key):
    with open(os.path.join(content, key + ".xnb"), "rb") as f:
        data = f.read()
    tex = xnb.parse(data)
    if not tex.standard_layout or tex.data_offset != xnb.EXPECTED_DATA_OFFSET:
        raise RuntimeError("%s: not the validated layout" % key)
    block = xnb.pixels(data)
    arr = np.frombuffer(block, np.uint8).reshape(tex.height, tex.width, 4).astype(np.int32)
    if (arr[arr[..., 3] == 0] != 0).any():
        raise RuntimeError("%s: a transparent pixel is not (0,0,0,0)" % key)
    return data, block, tex, arr


def bbox(mask):
    ys, xs = np.nonzero(mask)
    if not len(ys):
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def union(*boxes):
    boxes = [b for b in boxes if b]
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def shift(mask, dy, dx):
    """mask moved by (dy, dx), False where nothing comes from."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    ys = slice(max(dy, 0), h + min(dy, 0))
    yd = slice(max(-dy, 0), h + min(-dy, 0))
    xs = slice(max(dx, 0), w + min(dx, 0))
    xd = slice(max(-dx, 0), w + min(-dx, 0))
    out[ys, xs] = mask[yd, xd]
    return out


def erode(mask, n):
    out = mask.copy()
    for dy in range(-n, n + 1):
        for dx in range(-n, n + 1):
            out &= shift(mask, dy, dx)
    return out


def touches(mask, other):
    """pixels of `mask` with an 8-neighbour in `other`."""
    nb = np.zeros_like(mask)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy or dx:
                nb |= shift(other, dy, dx)
    return mask & nb


def components(mask):
    """8-connected components of a boolean mask, as a list of boolean masks."""
    h, w = mask.shape
    seen = np.zeros_like(mask)
    out = []
    for y0, x0 in zip(*[v.tolist() for v in np.nonzero(mask)]):
        if seen[y0, x0]:
            continue
        comp = np.zeros_like(mask)
        q = deque([(y0, x0)])
        seen[y0, x0] = True
        while q:
            y, x = q.popleft()
            comp[y, x] = True
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and mask[yy, xx] and not seen[yy, xx]:
                        seen[yy, xx] = True
                        q.append((yy, xx))
        out.append(comp)
    out.sort(key=lambda m: (-int(m.sum()),) + bbox(m))
    return out


def rgb(arr):
    return arr[..., :3]


def colour_is(arr, c):
    return (rgb(arr) == np.array(c)).all(-1)


def mode_colour(arr, mask):
    cnt = Counter(map(tuple, rgb(arr)[mask].tolist()))
    return cnt.most_common(1)[0][0] if cnt else None


def near_white(arr):
    return rgb(arr).min(-1) >= WHITE_MIN


def rim_colour(arr, op):
    edge = touches(op, ~op)
    return mode_colour(arr, edge)


def body_fill(arr, op):
    """Most common colour that is neither the rim nor near-white."""
    rim = rim_colour(arr, op)
    return mode_colour(arr, op & ~near_white(arr) & ~colour_is(arr, rim)), rim


def grow(box, clean, limit=1):
    """Grow box by up to `limit` px per side while the new strip is all `clean`."""
    h, w = clean.shape
    x0, y0, x1, y1 = box
    for _ in range(limit):
        if x0 > 0 and clean[y0:y1 + 1, x0 - 1].all():
            x0 -= 1
        if x1 < w - 1 and clean[y0:y1 + 1, x1 + 1].all():
            x1 += 1
        if y0 > 0 and clean[y0 - 1, x0:x1 + 1].all():
            y0 -= 1
        if y1 < h - 1 and clean[y1 + 1, x0:x1 + 1].all():
            y1 += 1
    return (x0, y0, x1, y1)


def centred_square(rect, frac=0.54):
    x0, y0, x1, y1 = rect
    side = max(1, int(np.floor(frac * (x1 - x0 + 1) + 0.5)))
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    bx, by = int(np.floor(cx - side / 2.0 + 0.5)), int(np.floor(cy - side / 2.0 + 0.5))
    return (bx, by, bx + side - 1, by + side - 1)


def tup(box):
    return tuple(int(v) for v in box) if box is not None else None


def cell(kind, role, rect, clear, draw, fill, style, color, **extra):
    d = dict(kind=kind, role=role, rect=tup(rect), clear=tup(clear), draw=tup(draw),
             fill=tuple(int(v) for v in fill) if fill is not None else None,
             style=style, color=tuple(int(v) for v in color) if color is not None else None)
    d.update(extra)
    return d


# --------------------------------------------------------------------------- families

def glyph_mask(arr, op, fill, margin):
    """What differs from the body fill inside the shape eroded by `margin` (no highlights)."""
    inner = erode(op, margin) & ~near_white(arr)
    far = np.abs(rgb(arr) - np.array(fill)).sum(-1) > 30
    return inner & far


def disc_cells(arrs, roles, margin):
    """Face-button discs: siblings share the layout; one cell each with the union clear."""
    info = {}
    for key, arr in arrs.items():
        op = arr[..., 3] > 0
        fill, rim = body_fill(arr, op)
        g = glyph_mask(arr, op, fill, margin)
        info[key] = (op, fill, rim, g)
    shapes = set(bbox(v[0]) for v in info.values())
    if len(shapes) != 1:
        raise RuntimeError("face discs do not share one shape box: %r" % shapes)
    shape = shapes.pop()
    inner = erode(info[next(iter(info))][0], margin)
    ibox = bbox(inner)
    gu = union(*[bbox(v[3]) for v in info.values()])
    draw = centred_square(shape)
    cu = union(gu, draw)             # the wipe must cover the old letters and the new symbol
    clear = (max(cu[0], ibox[0]), max(cu[1], ibox[1]), min(cu[2], ibox[2]), min(cu[3], ibox[3]))
    out = {}
    for key, (op, fill, rim, g) in info.items():
        arr = arrs[key]
        gcol = mode_colour(arr, g)
        out[key] = [cell("disc", roles[key], shape, clear, draw, fill, "solid", gcol,
                         glyph=tup(bbox(g)), rim=tuple(int(v) for v in rim))]
    return out


def fam_tip_dpad(arr, role):
    op = arr[..., 3] > 0
    lit = op & colour_is(arr, ORANGE)
    body = bbox(op & ~lit)
    cells = [cell("dpad", role, body, None, None, None, "keep", None)]
    comps = components(lit)
    for comp in comps:
        b = bbox(comp)
        inside = b[0] >= body[0] and b[2] <= body[2] and b[1] >= body[1] and b[3] <= body[3]
        cells.append(cell("lit", role, b, None, None, None, "keep", ORANGE,
                          part="pointer" if inside else "ray", pixels=int(comp.sum())))
    return cells


def fam_tip_stick(arr, role):
    op = arr[..., 3] > 0
    lit = op & colour_is(arr, ORANGE)
    comps = components(lit)
    if role == "lstick_neutral":
        if comps:
            raise RuntimeError("neutral stick has orange pixels")
        return [cell("stick", role, bbox(op), None, None, None, "keep", None)]
    if len(comps) != 2:
        raise RuntimeError("%s: expected ring + arrow, got %d orange parts" % (role, len(comps)))
    ring, arrow = comps                      # sorted by size: the ring is the biggest
    # the ring replaces pixels of the stick body, so only the arrow is outside the body
    cells = [cell("stick", role, bbox(op & ~arrow), None, None, None, "keep", None)]
    for comp, part in ((ring, "ring"), (arrow, "arrow")):
        cells.append(cell("lit", role, bbox(comp), None, None, None, "keep", ORANGE,
                          part=part, pixels=int(comp.sum())))
    return cells


def split_label(arr, op):
    comps = components(op)
    shape = comps[0]
    rest = np.zeros_like(op)
    for c in comps[1:]:
        rest |= c
    return shape, rest, len(comps) - 1


def fam_tip_label(arr, role):
    op = arr[..., 3] > 0
    shape, text, n = split_label(arr, op)
    if n < 2:
        raise RuntimeError("%s: label has %d letters" % (role, n))
    tb = bbox(text)
    sb = bbox(shape)
    clear = grow(tb, ~op)
    return [cell(LABEL_KIND[role], role, union(sb, tb), clear, tb, None, "solid",
                 mode_colour(arr, text), shape=tup(sb), text=tup(tb), letters=n)]


def fam_tip_start(arr, role):
    op = arr[..., 3] > 0
    icon, text, n = split_label(arr, op)
    ib, tb = bbox(icon), bbox(text)
    if not ib[3] < tb[1]:
        raise RuntimeError("START label is not under the icon")
    fill, rim = body_fill(arr, icon)
    tri = erode(icon, 2) & colour_is(arr, rim)
    gb = bbox(tri)
    return [cell("pill", role, ib, gb, gb, fill, "solid", rim, glyph=tup(gb)),
            cell("label", role, tb, grow(tb, ~op), tb, None, "solid", mode_colour(arr, text),
                 letters=n)]


def fam_tip_text(arr, role):
    op = arr[..., 3] > 0
    tb = bbox(op)
    return [cell("label", role, tb, grow(tb, ~op), tb, None, "solid", mode_colour(arr, op))]


def fam_menu_arrow(arr, role):
    op = arr[..., 3] > 0
    return [cell("arrow", role, bbox(op), None, None, None, "keep", None)]


def white_text(arr, op):
    return op & colour_is(arr, (255, 255, 255)) & (arr[..., 3] == 255)


def fam_menu_label(arr, role):
    op = arr[..., 3] > 0
    fill, rim = body_fill(arr, op)
    t = white_text(arr, op)
    tb = bbox(t)
    clean = op & colour_is(arr, fill)
    return [cell(LABEL_KIND[role], role, bbox(op), grow(tb, clean | t), tb, fill, "solid",
                 (255, 255, 255), text=tup(tb))]


def fam_menu_symbol(arr, role):
    op = arr[..., 3] > 0
    fill, rim = body_fill(arr, op)
    t = white_text(arr, op)
    tb = bbox(t)
    clean = op & colour_is(arr, fill)
    return [cell("pill", role, bbox(op), grow(tb, clean | t), tb, fill, "solid",
                 (255, 255, 255), glyph=tup(tb))]


def fam_menu_stick(arr, role):
    op = arr[..., 3] > 0
    fill, rim = body_fill(arr, op)
    t = white_text(arr, op)
    tb = bbox(t)
    clean = op & colour_is(arr, fill)
    return [cell("stick", role, bbox(op), grow(tb, clean | t), tb, fill, "solid",
                 (255, 255, 255), glyph=tup(tb))]


SINGLE = {
    "tip_dpad": fam_tip_dpad, "tip_stick": fam_tip_stick, "tip_label": fam_tip_label,
    "tip_start": fam_tip_start, "tip_text": fam_tip_text, "menu_arrow": fam_menu_arrow,
    "menu_label": fam_menu_label, "menu_symbol": fam_menu_symbol,
    "menu_stick": fam_menu_stick,
}
DISC_MARGIN = {"tip_face": 2, "menu_face": 2}


def build(content):
    """-> list of texture records (dicts) in SPEC + EXTRA order."""
    loaded = {}
    for key, fam, role, ps, act in SPEC + EXTRA:
        loaded[key] = load(content, key)
    cells = {}
    for fam in ("tip_face", "menu_face"):
        keys = [s for s in SPEC if s[1] == fam]
        arrs = dict((s[0], loaded[s[0]][3]) for s in keys)
        roles = dict((s[0], s[2]) for s in keys)
        cells.update(disc_cells(arrs, roles, DISC_MARGIN[fam]))
    recs = []
    for key, fam, role, ps, act in SPEC + EXTRA:
        data, block, tex, arr = loaded[key]
        cs = cells[key] if fam in DISC_MARGIN else SINGLE[fam](arr, role)
        recs.append(dict(
            key=key, width=tex.width, height=tex.height, pixel_offset=tex.data_offset,
            size=len(data), pixel_size=tex.pixel_size,
            sha256=hashlib.sha256(data).hexdigest(),
            pixels_sha256=hashlib.sha256(block).hexdigest(),
            family=fam, role=role, ps_icon=ps, ps_action=act, cells=cs,
            extra=(key, fam, role, ps, act) in EXTRA))
    check(recs)
    return recs


def check(recs):
    """Refuse a layout we do not understand."""
    for r in recs:
        assert r["size"] == r["pixel_offset"] + r["width"] * r["height"] * 4, r["key"]
        assert r["pixel_size"] == r["width"] * r["height"] * 4, r["key"]
        for c in r["cells"]:
            for name in ("rect", "clear", "draw"):
                b = c[name]
                if b is None:
                    continue
                assert 0 <= b[0] <= b[2] < r["width"] and 0 <= b[1] <= b[3] < r["height"], \
                    (r["key"], name, b)
                assert b[2] - b[0] >= 0 and b[3] - b[1] >= 0, (r["key"], name, b)
            if c["clear"] and c["draw"]:
                cl, dr = c["clear"], c["draw"]
                assert cl[0] <= dr[0] and cl[1] <= dr[1] and cl[2] >= dr[2] and cl[3] >= dr[3] \
                    or c["kind"] == "disc", (r["key"], "draw outside clear", cl, dr)
    # the sibling groups must really share a layout
    for fam in ("tip_face", "menu_face"):
        rects = set(tuple(r["cells"][0]["rect"]) for r in recs if r["family"] == fam)
        assert len(rects) == 1, (fam, rects)


# --------------------------------------------------------------------------- output

HEADER = '''"""Generated by tools/sfh_build.py from the original textures - do not edit.

Songs for a Hero - Definitive Edition (Steam 389170). Per texture: path relative to the
game's Content folder (add ".xnb"), size, pixel block offset/size, SHA-256 of the WHOLE
original file and of its pixel block, the role, what a PlayStation pad would need
(ps_icon / ps_action) and the button cells.

Pixels are R,G,B,A in memory (XNB / MonoGame Color, NOT B,G,R like the DDS profiles), rows
top-down, straight alpha; a transparent pixel is (0,0,0,0). So every colour below is R,G,B.
Rects are inclusive (x0, y0, x1, y1) in texture pixels: rect = whole shape, clear = area
wiped (None = nothing to wipe), draw = where the new symbol/label goes, fill = body colour
used to wipe (None = wipe to transparent), style = how the old glyph is drawn (solid = one
colour, keep = leave alone), color = glyph colour. Cell kinds: disc (face button), bump /
trig (shoulder / trigger label), pill (menu button), label (text), stick, dpad, arrow, lit
(highlighted part of a tooltip D-pad / stick: part = ray | pointer | ring | arrow).
ps_action: redraw = new symbol, relabel = new text, same = identical art on a PlayStation
pad (leave), optional = Xbox-360 look a PS pad shows differently (harmless to keep).
EXTRA_TEXTURES are present but not referenced by name in DumaLegend.exe."""

'''


def render(recs):
    lines = [HEADER.rstrip("\n"), "", "TEXTURES = ["]
    extra = []
    for part, bucket in ((False, lines), (True, extra)):
        if part:
            bucket += ["", "EXTRA_TEXTURES = ["]
        for r in recs:
            if r["extra"] != part:
                continue
            bucket += ["    {",
                       '        "key": "%s",' % r["key"],
                       '        "width": %d, "height": %d, "pixel_offset": %d,' % (
                           r["width"], r["height"], r["pixel_offset"]),
                       '        "size": %d, "pixel_size": %d,' % (r["size"], r["pixel_size"]),
                       '        "sha256": "%s",' % r["sha256"],
                       '        "pixels_sha256": "%s",' % r["pixels_sha256"],
                       '        "family": "%s", "role": "%s",' % (r["family"], r["role"]),
                       '        "ps_icon": %r, "ps_action": "%s",' % (r["ps_icon"], r["ps_action"]),
                       '        "cells": [']
            bucket += ["            %r," % (c,) for c in r["cells"]]
            bucket += ["        ],", "    },"]
        bucket.append("]")
    return "\n".join(lines + extra) + "\n"


# --------------------------------------------------------------------------- contact sheets

OVERLAY = {"rect": (255, 255, 255), "clear": (0, 220, 255), "draw": (255, 0, 200)}


def sheets(content, recs, out_dir):
    from PIL import Image, ImageDraw
    os.makedirs(out_dir, exist_ok=True)
    groups = [("sfh_tooltip", [r for r in recs if r["key"].startswith("Tooltip/")], 10, 5),
              ("sfh_menu", [r for r in recs if r["key"].startswith("Menu/")], 14, 6)]
    for name, rs, S, cols in groups:
        tw = max(r["width"] for r in rs) * S + 16
        th = max(r["height"] for r in rs) * S + 30
        rows = (len(rs) + cols - 1) // cols
        im = Image.new("RGB", (cols * tw, rows * th + 20), (40, 40, 48))
        dr = ImageDraw.Draw(im)
        dr.text((6, 4), "white=rect  cyan=clear  magenta=draw  green=lit part  (x%d, top-down)"
                % S, fill=(200, 200, 200))
        for i, r in enumerate(rs):
            _, _, tex, arr = load(content, r["key"])
            ox, oy = (i % cols) * tw + 8, (i // cols) * th + 36
            pic = Image.fromarray(arr.astype(np.uint8)).convert("RGBA").resize(
                (tex.width * S, tex.height * S), Image.NEAREST)
            im.paste(pic, (ox, oy), pic)
            dr.text((ox, oy - 14), r["key"] + (" (EXTRA)" if r["extra"] else ""),
                    fill=(255, 255, 0))
            for c in r["cells"]:
                for nm in ("rect", "clear", "draw"):
                    b = c[nm]
                    if not b or (nm == "rect" and c["kind"] in ("lit",)):
                        continue
                    col = (0, 255, 0) if c["kind"] == "lit" else OVERLAY[nm]
                    dr.rectangle([ox + b[0] * S - 1, oy + b[1] * S - 1,
                                  ox + (b[2] + 1) * S, oy + (b[3] + 1) * S], outline=col)
                if c["kind"] == "lit":
                    b = c["rect"]
                    dr.rectangle([ox + b[0] * S - 1, oy + b[1] * S - 1,
                                  ox + (b[2] + 1) * S, oy + (b[3] + 1) * S], outline=(0, 255, 0))
        path = os.path.join(out_dir, name + ".png")
        im.save(path)
        print("sheet", path, im.size)


def main():
    args = sys.argv[1:]
    sheet_dir = None
    if "--sheets" in args:
        i = args.index("--sheets")
        sheet_dir = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        print(__doc__)
        return 2
    content = args[0]
    recs = build(content)
    for r in recs:
        print("%-28s %2dx%-2d %-14s %-9s %-8s %d cell(s)%s" % (
            r["key"], r["width"], r["height"], r["role"], r["ps_icon"], r["ps_action"],
            len(r["cells"]), "  [EXTRA]" if r["extra"] else ""))
    with open(OUT_PY, "w") as f:
        f.write(render(recs))
    print("wrote", OUT_PY, "-", sum(1 for r in recs if not r["extra"]), "textures,",
          sum(len(r["cells"]) for r in recs if not r["extra"]), "cells (+",
          sum(1 for r in recs if r["extra"]), "extra)")
    if sheet_dir:
        sheets(content, recs, sheet_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
