"""Glyphs stored as DXT5 textures at fixed offsets inside a huge archive.

Same contract as region_glyphs.RegionGlyphProfile (see region_patch.py): every region is a
whole DDS file (128-byte header + base level, no mipmaps) at a known offset, with the
SHA-256 of its original bytes; patching rewrites it in place with the same size.

Where region_glyphs redraws raw BGRA atlases, here a region is DXT5, so only the 4x4
blocks under the cells are decoded, redrawn and re-encoded; every other block stays
byte-identical.

A layout is a list of cells, in texture pixels (inclusive rectangles):
  clear   area wiped to transparent before drawing (widened to whole 4x4 blocks)
  draws   [(rect, icon)] - icon is a bundled PNG ("ps/outline-red-circle") fitted into rect
"""
import os

from .. import image

HEADER = 128
GLYPH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "glyphs")


def _blocks(rect):
    x0, y0, x1, y1 = rect
    return x0 // 4, y0 // 4, x1 // 4 + 1, y1 // 4 + 1


class DxtRegionProfile:
    kind = "dxt-regions"
    region_level = True

    def __init__(self, appid, name, file, regions, layouts, roles, native_style, notes="",
                 revision=1, original_hint="", support="partial", styles=("ps",), glyph_dir=GLYPH_DIR):
        self.appid = appid
        self.name = name
        self.file = file                  # archive, relative to the install dir
        self.regions = regions            # [{key, offset, size, width, height, sha256, layout}]
        self.layouts = layouts            # {layout: [cell]}
        self.roles = roles
        self.native_style = native_style  # the style the game already shows ("xbox")
        self.revision = revision
        self.support = support        # "full" (confirmed in game) or "partial": shown in the UI
        self.notes = notes
        self.original_hint = original_hint
        self.patch_styles = tuple(styles)
        self.styles = {}                  # no icon sets for the UI check
        self.textures = []
        self.glyph_dir = glyph_dir
        self._icons = {}

    def _icon(self, name, w, h):
        key = (name, w, h)
        if key not in self._icons:
            rgba, iw, ih = image.read_png(os.path.join(self.glyph_dir, name + ".png"))
            self._icons[key] = image.fit_icon(rgba, iw, ih, w, h)
        return self._icons[key]

    def preflight(self):
        """Load and fit every icon the layouts draw BEFORE the first byte is written."""
        for region in self.regions:
            key = region["key"]
            for cell in self.layouts[region["layout"]]:
                for rect, name in cell["draws"]:
                    x0, y0, x1, y1 = rect
                    try:
                        if x1 < x0 or y1 < y0:
                            raise ValueError("empty draw box")
                        self._icon(name, x1 - x0 + 1, y1 - y0 + 1)
                    except Exception as exc:
                        raise RuntimeError("%s: bundled icon %r is missing or damaged (%s)"
                                           % (key, name, exc))

    # ------------------------------------------------------------------ rendering

    def _draw_cell(self, body, tex_w, cell):
        bx0, by0, bx1, by1 = _blocks(cell["clear"])
        bw, bh = bx1 - bx0, by1 - by0
        rw, rh = bw * 4, bh * 4
        rgba = bytearray(rw * rh * 4)                       # wiped: transparent
        for rect, name in cell["draws"]:
            x0, y0, x1, y1 = rect
            icon, iw, ih = self._icon(name, x1 - x0 + 1, y1 - y0 + 1)
            ox = x0 - bx0 * 4 + (x1 - x0 + 1 - iw) // 2
            oy = y0 - by0 * 4 + (y1 - y0 + 1 - ih) // 2
            for y in range(ih):
                for x in range(iw):
                    k = (y * iw + x) * 4
                    if icon[k + 3]:
                        d = ((oy + y) * rw + ox + x) * 4
                        rgba[d:d + 4] = icon[k:k + 4]
        row = tex_w // 4 * 16
        for j in range(bh):
            for i in range(bw):
                px = image.block_pixels(rgba, rw, i, j)
                solid = [p for p in px if p[3]]
                if solid:       # keep transparent pixels the colour of their neighbours (no dark fringe)
                    mean = tuple(sum(p[c] for p in solid) // len(solid) for c in range(3))
                    px = [p if p[3] else mean + (0,) for p in px]
                o = (by0 + j) * row + (bx0 + i) * 16
                body[o:o + 16] = image.encode_block(px, "DXT5")

    def render(self, original, region, style):
        """New bytes of a region for `style`, from the original bytes (marker not included)."""
        if style not in self.patch_styles:
            raise ValueError("unknown style %r" % style)
        if len(original) != region["size"]:
            raise ValueError("%s: wrong size" % region["key"])
        w, h = region["width"], region["height"]
        body = bytearray(original[HEADER:])
        if len(body) != w * h:
            raise ValueError("%s: not a single-level DXT5 texture" % region["key"])
        for cell in self.layouts[region["layout"]]:
            self._draw_cell(body, w, cell)
        return bytes(original[:HEADER]) + bytes(body)
