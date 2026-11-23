"""Glyphs stored in DXT textures inside a Scimitar .forge data file.

A profile of this kind lists, per texture, the boxes where each button glyph is
drawn. Patching clears those boxes and draws the chosen icon set in them; every
other pixel (and every other block of the texture) is left byte-identical.

Per-texture options:
  flip_y     the texture stores rows bottom-up (TGA-sourced font atlases), so icons
             are mirrored vertically before drawing to show upright in game
  tint       (r, g, b) - recolour icons, e.g. white for a monochrome font
  icons      {style: {role: icon}} overriding the profile-wide icons for this texture
  clear_all  wipe the whole texture before drawing (single-glyph HUD textures)
"""
import struct

from .. import image
from ..forge import Forge, ForgeError, append_entry_payload

TEX_DATA_MARK = bytes.fromhex("e97f2313")
FORMATS = {1: "DXT1", 2: "DXT1", 3: "DXT1", 4: "DXT3", 5: "DXT5"}
MIN_GLYPH_PIXELS = 40  # coloured pixels expected inside an original glyph box


class ForgeGlyphProfile:
    kind = "forge-glyphs"

    def __init__(self, appid, name, file, datafile, textures, roles, styles, notes="",
                 revision=1):
        self.appid = appid
        self.revision = revision    # bump when the output changes, so applied files get redone
        self.name = name
        self.file = file            # path relative to the game's install dir
        self.datafile = datafile    # forge entry holding the textures
        self.textures = textures    # [{id, label, width, height, format, boxes{role: box}, clear_all?}]
        self.roles = roles          # [(role, label)]
        self.styles = styles        # {style: {role: icon path "set/name"}}
        self.notes = notes

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _layout(res):
        nl = struct.unpack_from("<I", res, 8)[0]
        p = 12 + nl + 1 + 8
        w, h, _, fmt = struct.unpack_from("<4I", res, p)
        mark = res.find(TEX_DATA_MARK, p)
        if mark < 0:
            raise ForgeError("texture data marker not found")
        size = struct.unpack_from("<I", res, mark + 4)[0]
        return w, h, fmt, mark + 8, size

    def _texture(self, df, spec):
        res = df.read_resource(spec["id"])
        w, h, fmt, off, size = self._layout(res)
        name = FORMATS.get(fmt)
        if (w, h) != (spec["width"], spec["height"]) or name != spec["format"]:
            raise ForgeError("%s: expected %dx%d %s, found %dx%d fmt %d" % (
                spec["label"], spec["width"], spec["height"], spec["format"], w, h, fmt))
        bpb = 8 if name == "DXT1" else 16
        top = (w // 4) * (h // 4) * bpb
        if size < top:
            raise ForgeError("%s: pixel data too small" % spec["label"])
        return res, off, top, name

    # ------------------------------------------------------------------ API

    def check(self, path):
        """Problems that make `path` unsafe to patch (empty list = OK). Expects an original file."""
        problems = []
        try:
            df = Forge(path).load(self.datafile)
        except (ForgeError, OSError, struct.error) as exc:
            return ["cannot read %s: %s" % (self.file, exc)]
        for spec in self.textures:
            try:
                res, off, top, fmt = self._texture(df, spec)
            except (ForgeError, struct.error) as exc:
                problems.append(str(exc))
                continue
            rgba = image.decode(res[off:off + top], spec["width"], spec["height"], fmt)
            w = spec["width"]
            for role, (x0, y0, x1, y1) in spec["boxes"].items():
                coloured = 0
                for y in range(y0, y1 + 1):
                    for x in range(x0, x1 + 1):
                        o = (y * w + x) * 4
                        r, g, b, a = rgba[o:o + 4]
                        if a > 100 and (spec.get("tint") or max(r, g, b) - min(r, g, b) > 50):
                            coloured += 1
                if coloured < MIN_GLYPH_PIXELS:
                    problems.append("%s: no %s glyph where expected" % (spec["label"], role))
        return problems

    def patch(self, path, style, load_icon):
        """Draw `style` glyphs into the original file at `path` (modified in place)."""
        if style not in self.styles:
            raise ValueError("unknown style %r" % style)
        fg = Forge(path)
        entry = fg.find(self.datafile)
        df = fg.load(self.datafile)
        for spec in self.textures:
            icons = spec.get("icons", {}).get(style, self.styles[style])
            self._patch_texture(df, spec, icons, load_icon)
        append_entry_payload(path, entry, df.tobytes())

    @staticmethod
    def _adjust(icon, iw, ih, spec):
        tint = spec.get("tint")
        if tint:
            out = bytearray(icon)
            for i in range(0, len(out), 4):
                out[i:i + 3] = bytes(tint)
            icon = bytes(out)
        if spec.get("flip_y"):
            row = iw * 4
            icon = b"".join(icon[y * row:(y + 1) * row] for y in range(ih - 1, -1, -1))
        return icon

    def _patch_texture(self, df, spec, icons, load_icon):
        res, off, top, fmt = self._texture(df, spec)
        w, h = spec["width"], spec["height"]
        raw = bytearray(res[off:off + top])
        rgba = bytearray(image.decode(bytes(raw), w, h, fmt))
        touched = set()

        def clear(x0, y0, x1, y1):
            for y in range(max(0, y0), min(h - 1, y1) + 1):
                for x in range(max(0, x0), min(w - 1, x1) + 1):
                    o = (y * w + x) * 4
                    rgba[o:o + 4] = b"\0\0\0\0"
                    touched.add((x // 4, y // 4))

        if spec.get("clear_all"):
            clear(0, 0, w - 1, h - 1)
        for x0, y0, x1, y1 in spec["boxes"].values():
            clear(x0 - 1, y0 - 1, x1 + 1, y1 + 1)
        for role, (x0, y0, x1, y1) in spec["boxes"].items():
            bw, bh = x1 - x0 + 1, y1 - y0 + 1
            icon, iw, ih = load_icon(icons[role], bw, bh)
            icon = self._adjust(icon, iw, ih, spec)
            ox, oy = x0 + (bw - iw) // 2, y0 + (bh - ih) // 2
            for y in range(ih):
                for x in range(iw):
                    s = (y * iw + x) * 4
                    if icon[s + 3]:
                        d = ((oy + y) * w + ox + x) * 4
                        rgba[d:d + 4] = icon[s:s + 4]
        bpb = 8 if fmt == "DXT1" else 16
        for bx, by in touched:
            k = (by * (w // 4) + bx) * bpb
            raw[k:k + bpb] = image.encode_block(image.block_pixels(rgba, w, bx, by), fmt)
        df.patch_resource(spec["id"], off, bytes(raw))
