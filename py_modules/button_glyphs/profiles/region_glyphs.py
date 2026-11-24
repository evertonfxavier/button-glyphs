"""Glyphs stored as raw BGRA textures at fixed offsets inside a huge archive.

Some archives are far too big to copy or rewrite (Quantum Break's ep999-000.rmdp is 56 GB),
but store their textures uncompressed and unmoved. A profile of this kind lists those
textures as *regions* (offset, size, SHA-256 of the original bytes) and, per region, the
button cells to redraw. Patching rewrites the region in place with the same size, so the
rest of the archive - and its index - is never touched.

Each region starts with a 128-byte DDS header whose reserved fields (0x20..0x4B) are zero
in the original; we keep our marker there (see region_patch.py).

A cell is one button shape in the atlas:
  kind   disc (round face button) | letter (bare glyph) | trig/bump (trapezoid with a label)
  rect   the whole shape, clear the area wiped, draw where the new symbol/label goes
  fill   colour of the shape (B, G, R) used to wipe it; None for bare letters
  style  how the old glyph was drawn: hole (transparent), solid (one colour), ps (role colour)
  color  colour of a "solid" glyph (B, G, R)
Colours are in pixel order (B, G, R).

A region may instead carry `replace` = {image, box, clear}: the whole picture is swapped for a
bundled RGBA drawing (see `_replace_picture`). Such a region has no cells.
"""
import os

from .. import image

HEADER = 128
MASK_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "masks")


def _bgr(rgb):
    return (rgb[2], rgb[1], rgb[0])


# PlayStation symbol colours, as drawn on a DualShock-style prompt
PS_COLORS = {
    "a": _bgr((110, 168, 254)),   # cross    - blue
    "b": _bgr((255, 75, 75)),     # circle   - red
    "x": _bgr((230, 122, 203)),   # square   - pink
    "y": _bgr((52, 184, 153)),    # triangle - green
}
SYMBOL = {"a": "cross", "b": "circle", "x": "square", "y": "triangle"}
LABEL = {"lt": "L2", "rt": "R2", "lb": "L1", "rb": "R1"}


class MaskLoader:
    """Loads the bundled white-on-transparent masks and scales them to a box (cached)."""

    def __init__(self, mask_dir=MASK_DIR):
        self.mask_dir = mask_dir
        self.src = {}
        self.cache = {}

    def __call__(self, name, box_w, box_h):
        """-> (alpha bytes, w, h) of the mask fitted inside box_w x box_h."""
        key = (name, box_w, box_h)
        if key not in self.cache:
            if name not in self.src:
                self.src[name] = image.read_png(os.path.join(self.mask_dir, name + ".png"))
            rgba, w, h = self.src[name]
            fitted, nw, nh = image.fit_icon(rgba, w, h, box_w, box_h)
            self.cache[key] = (bytes(fitted[3::4]), nw, nh)
        return self.cache[key]


class PictureLoader:
    """Loads bundled full-colour drawings (RGBA PNG in masks/) once: -> (rgba, w, h)."""

    def __init__(self, mask_dir=MASK_DIR):
        self.mask_dir = mask_dir
        self.cache = {}

    def __call__(self, name):
        if name not in self.cache:
            self.cache[name] = image.read_png(os.path.join(self.mask_dir, name + ".png"))
        return self.cache[name]


class RegionGlyphProfile:
    kind = "regions"
    region_level = True

    def __init__(self, appid, name, file, regions, roles, native_style, notes="",
                 revision=1, original_hint="", support="partial", styles=("ps",)):
        self.appid = appid
        self.name = name
        self.file = file                  # archive, relative to the install dir
        self.regions = regions            # [{key, offset, size, width, height, sha256, cells}]
        self.roles = roles
        self.native_style = native_style  # the style the game already shows ("xbox")
        self.revision = revision
        self.support = support        # "full" (confirmed in game) or "partial": shown in the UI
        self.notes = notes
        self.original_hint = original_hint
        self.patch_styles = tuple(styles)
        self.styles = {}                  # no bundled icon sets for the UI check
        self.textures = []
        self.masks = MaskLoader()
        self.pictures = PictureLoader()

    # ------------------------------------------------------------------ preflight

    def preflight(self):
        """Validate every bundled asset and box BEFORE the first byte is written.

        Loads each picture and mask the regions use (the loaders cache them, so render() reuses
        the result) and checks the geometry. Raises RuntimeError with a clear message, so a
        missing or damaged PNG fails the whole apply instead of stopping it halfway.
        """
        for region in self.regions:
            key, w, h = region["key"], region["width"], region["height"]
            if region["size"] != HEADER + w * h * 4:
                raise RuntimeError("%s: size does not match a %dx%d BGRA texture" % (key, w, h))
            spec = region.get("replace")
            if spec:
                x0, y0, x1, y1 = spec["box"]
                if not (0 <= x0 < x1 < w and 0 <= y0 < y1 < h):
                    raise RuntimeError("%s: picture box lies outside the texture" % key)
                try:
                    rgba, pw, ph = self.pictures(spec["image"])
                    if pw < 1 or ph < 1 or len(rgba) != pw * ph * 4:
                        raise ValueError("pixel data does not match %dx%d" % (pw, ph))
                except Exception as exc:
                    raise RuntimeError("%s: bundled picture %r is missing or damaged (%s)"
                                       % (key, spec["image"], exc))
                if pw > x1 - x0 + 1 or ph > y1 - y0 + 1:
                    raise RuntimeError("%s: picture %r (%dx%d) does not fit its box"
                                       % (key, spec["image"], pw, ph))
            for cell in region["cells"]:
                name = SYMBOL[cell["role"]] if cell["kind"] in ("disc", "letter") \
                    else LABEL[cell["role"]]
                dx0, dy0, dx1, dy1 = cell["draw"]
                try:
                    if dx1 < dx0 or dy1 < dy0:
                        raise ValueError("empty draw box")
                    self.masks(name, dx1 - dx0 + 1, dy1 - dy0 + 1)
                except Exception as exc:
                    raise RuntimeError("%s: bundled mask %r is missing or damaged (%s)"
                                       % (key, name, exc))

    # ------------------------------------------------------------------ rendering

    def _draw_cell(self, buf, w, h, cell):
        kind, role = cell["kind"], cell["role"]
        fill, style = cell["fill"], cell["style"]
        cx0, cy0, cx1, cy1 = cell["clear"]
        cx0, cy0, cx1, cy1 = max(0, cx0), max(0, cy0), min(w - 1, cx1), min(h - 1, cy1)
        wipe = bytes(fill) + b"\xff" if fill is not None else b"\0\0\0\0"
        row = wipe * (cx1 - cx0 + 1)
        for y in range(cy0, cy1 + 1):
            o = HEADER + (y * w + cx0) * 4
            buf[o:o + len(row)] = row

        name = SYMBOL[role] if kind in ("disc", "letter") else LABEL[role]
        dx0, dy0, dx1, dy1 = cell["draw"]
        bw, bh = dx1 - dx0 + 1, dy1 - dy0 + 1
        alpha, mw, mh = self.masks(name, bw, bh)
        ox, oy = dx0 + (bw - mw) // 2, dy0 + (bh - mh) // 2
        color = PS_COLORS[role] if style == "ps" else tuple(cell["color"])
        base = fill if fill is not None else color
        for my in range(mh):
            y = oy + my
            if not 0 <= y < h:
                continue
            for mx in range(mw):
                a = alpha[my * mw + mx]
                x = ox + mx
                if not a or not 0 <= x < w:
                    continue
                o = HEADER + (y * w + x) * 4
                if fill is None:                       # bare letter: colour with coverage alpha
                    buf[o:o + 4] = bytes(color) + bytes((a,))
                elif style == "hole":                  # glyph is a hole in the shape
                    buf[o:o + 4] = bytes(base) + bytes((255 - a,))
                else:
                    buf[o:o + 4] = bytes(
                        (base[k] * (255 - a) + color[k] * a + 127) // 255 for k in range(3)
                    ) + b"\xff"

    def _replace_picture(self, buf, w, h, spec, key):
        """Swap the whole picture: clear the body to transparent, draw the bundled art in the box.

        The art is stored as straight (not premultiplied) RGBA, like the game's own pixels, and
        is centred in `box` (inclusive pixel rectangle) without resampling when it already has
        the final size. Fully transparent pixels get the colour the game keeps under them.
        The 128-byte header is not touched.
        """
        if w * h * 4 + HEADER != len(buf):
            raise ValueError("%s: not a %dx%d BGRA texture" % (key, w, h))
        x0, y0, x1, y1 = spec["box"]
        if not (0 <= x0 < x1 < w and 0 <= y0 < y1 < h):
            raise ValueError("%s: picture box outside the texture" % key)
        bw, bh = x1 - x0 + 1, y1 - y0 + 1
        rgba, pw, ph = self.pictures(spec["image"])
        if pw > bw or ph > bh:                   # larger than its box: fit (slow, never needed
            rgba, pw, ph = image.fit_icon(rgba, pw, ph, bw, bh)    # for the bundled art)
        ox, oy = x0 + (bw - pw) // 2, y0 + (bh - ph) // 2
        clear = bytes(spec["clear"]) + b"\0"
        buf[HEADER:] = clear * (w * h)
        for py in range(ph):
            row = rgba[py * pw * 4:(py + 1) * pw * 4]
            if not any(row[3::4]):
                continue
            o = HEADER + ((oy + py) * w + ox) * 4
            out = bytearray(buf[o:o + pw * 4])
            for px in range(pw):
                i = px * 4
                if row[i + 3]:
                    out[i:i + 4] = bytes((row[i + 2], row[i + 1], row[i], row[i + 3]))
            buf[o:o + pw * 4] = out

    def render(self, original, region, style):
        """New bytes of a region for `style`, from the original bytes (marker not included)."""
        if style not in self.patch_styles:
            raise ValueError("unknown style %r" % style)
        if len(original) != region["size"]:
            raise ValueError("%s: wrong size" % region["key"])
        buf = bytearray(original)
        if region.get("replace"):
            self._replace_picture(buf, region["width"], region["height"], region["replace"],
                                  region["key"])
        for cell in region["cells"]:
            self._draw_cell(buf, region["width"], region["height"], cell)
        return bytes(buf)
