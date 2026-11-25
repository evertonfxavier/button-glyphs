"""Games whose archives can be extended by one more archive (add-file overlay strategy, see overlay_patch.py).

A profile of this kind lists the ORIGINAL entries it replaces (`entries`: path inside the archives, size, SHA-256, the
archive that wins it) and, per entry, a *builder* that turns the game's own original bytes into the replacement.
Nothing is written into a game file: the engine adds `archive` (or, with the loose vehicle, the same entries as loose
files) and removes it again on Original.

An entry (`entries`) describes one original:
  key      name used in messages ("A_button")
  path     the virtual path inside the archives ("Content/SeriousSam2/Textures/Menu/A_button.tex")
  size     bytes of the original entry, uncompressed; sha256  its SHA-256 (the build this profile knows)
  gro      the archive that wins the entry in the supported build (a different winner is another build)
  width, height   the texture's size

A builder (`builders[key]`) has `art()` -> names of the bundled pictures it needs, `preflight(pictures)` and
`build(original, entry, style, pictures) -> bytes`; a builder proves its own output (it decodes it back and compares it
with the intended pixels) and raises RuntimeError when it does not hold. `outputs(style, originals)` calls them all
(memoised per style and revision: the status poll must not redraw every time; `preflight()` clears the memo).

Shared drawing helpers (round symbols in a stretched oval cell, a whole replacement picture, the proof that a
re-encoded .tex still reads as intended) live here so another Croteam game only needs a table and its builders.
"""
import hashlib
import os
import sys

from .. import croteam_tex, gro, image, overlay_patch
from .region_glyphs import PictureLoader

PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # art names are relative to it
TEXTURE_BYTES = 4


# ------------------------------------------------------------------ drawing helpers


def opaque_box(rgba, w, h, threshold=0):
    """(x0, y0, x1, y1) of the pixels with alpha > threshold (x1, y1 exclusive), or None when nothing is visible."""
    xs0, ys0, xs1, ys1 = w, h, -1, -1
    for y in range(h):
        row = rgba[y * w * 4:(y + 1) * w * 4]
        alpha = row[3::4]
        if not any(a > threshold for a in alpha):
            continue
        first = next(i for i, a in enumerate(alpha) if a > threshold)
        last = w - 1 - next(i for i, a in enumerate(reversed(alpha)) if a > threshold)
        xs0, xs1 = min(xs0, first), max(xs1, last)
        ys0, ys1 = min(ys0, y), max(ys1, y)
    return None if ys1 < 0 else (xs0, ys0, xs1 + 1, ys1 + 1)


def crop(rgba, w, h, box):
    x0, y0, x1, y1 = box
    out = b"".join(rgba[(y * w + x0) * 4:(y * w + x1) * 4] for y in range(y0, y1))
    return out, x1 - x0, y1 - y0


def bleed(rgba, w, h):
    """Straight RGBA whose fully transparent pixels carry the colour of the nearest visible pixel (alpha untouched).

    The HD textures store exactly that under alpha 0 (a white disc bleeds white, a green oval green): the colour a
    filtered sample mixes in at the edge is the art's own. Only for small canvases (every pass walks the whole picture)."""
    out = bytearray(rgba)
    todo = [i for i in range(w * h) if out[i * 4 + 3] == 0]
    have = [out[i * 4 + 3] > 0 for i in range(w * h)]
    if len(todo) == w * h:
        return bytes(out)
    while todo:
        found, rest = [], []
        for i in todo:
            x, y = i % w, i // w
            acc, n = [0, 0, 0], 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = x + dx, y + dy
                    if (dx or dy) and 0 <= nx < w and 0 <= ny < h and have[ny * w + nx]:
                        o = (ny * w + nx) * 4
                        acc[0] += out[o]
                        acc[1] += out[o + 1]
                        acc[2] += out[o + 2]
                        n += 1
            if n:
                found.append((i, bytes((acc[0] // n, acc[1] // n, acc[2] // n))))
            else:
                rest.append(i)
        if not found:
            break
        for i, c in found:                                   # applied after the pass: no directional smear
            out[i * 4:i * 4 + 3] = c
            have[i] = True
        todo = rest
    return bytes(out)


def round_glyph(icon, iw, ih, canvas_w, canvas_h, cell_box, clear, diameter=None, fit="round"):
    """The icon, cropped to its visible part, fitted and centred on the centre of `cell_box`, on a transparent canvas.

    `cell_box` = (x0, y0, x1, y1) of the ORIGINAL art (an oval or a disc inside a square texture, measured on the
    original). fit="round": into a square of `diameter` (default: the SHORT side of the box); fit="box": into the
    whole box, aspect kept (shoulders, triggers, pills). Centre = floor on odd sizes.
    `clear` = (r, g, b) stored under alpha 0 (the game's own convention for that texture), or "bleed" (the colour of the
    nearest visible pixel). Straight RGBA out.
    """
    bx0, by0, bx1, by1 = cell_box
    vis = opaque_box(icon, iw, ih)
    if vis is None:
        raise ValueError("the icon has no visible pixel")
    cropped, cw, ch = crop(icon, iw, ih, vis)
    if fit == "box":
        fitted, fw, fh = image.fit_icon(cropped, cw, ch, bx1 - bx0, by1 - by0)
    elif fit == "round":
        d = diameter or min(bx1 - bx0, by1 - by0)
        fitted, fw, fh = image.fit_icon(cropped, cw, ch, d, d)
    else:
        raise ValueError("unknown fit %r" % (fit,))
    ox, oy = (bx0 + bx1 - fw) // 2, (by0 + by1 - fh) // 2
    if ox < 0 or oy < 0 or ox + fw > canvas_w or oy + fh > canvas_h:
        raise ValueError("the symbol does not fit the canvas")
    canvas = bytearray(bytes((0, 0, 0) if clear == "bleed" else clear) + b"\0") * (canvas_w * canvas_h)
    for y in range(fh):
        src = fitted[y * fw * 4:(y + 1) * fw * 4]
        dst = ((oy + y) * canvas_w + ox) * 4
        for x in range(fw):
            p = src[x * 4:x * 4 + 4]
            if p[3]:
                canvas[dst + x * 4:dst + x * 4 + 4] = p
    return bleed(bytes(canvas), canvas_w, canvas_h) if clear == "bleed" else bytes(canvas)


def normalise_clear(rgba, clear):
    """Every fully transparent pixel gets the colour `clear` (the game's convention); the others are kept."""
    out = bytearray(rgba)
    c = bytes(clear)
    for i in range(0, len(out), 4):
        if out[i + 3] == 0:
            out[i:i + 3] = c
    return bytes(out)


LOSSY_MEAN = 12.0       # mean |error| per channel (0..255) a DXT round trip of flat icons may show


def mean_error(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


def check_reencoded(original, new, expect, what, lossy=False):
    """Prove `new` is the game's own .tex with only its pixels swapped, and that those pixels are `expect`.

    Same head, tail, size, format; the pixel block parses; level 0 decodes back to exactly `expect` (raw formats are
    lossless) or, for DXT (`lossy`), within LOSSY_MEAN of it; the first mip level is the half-size of the same picture.
    """
    a, b = croteam_tex.parse(original), croteam_tex.parse(new)
    if b.tobytes() != new:
        raise RuntimeError("%s: the rewritten texture does not re-serialise" % what)
    if (a.width, a.height, a.fmt, a.fmt_name) != (b.width, b.height, b.fmt, b.fmt_name) or a.head != b.head \
            or a.tail != b.tail or (a.kind == "hd" and len(a.stored) != len(b.stored)):
        raise RuntimeError("%s: the rewrite changed more than the pixel block" % what)
    got = b.pixels()[0]
    if not lossy:
        if got != expect:
            raise RuntimeError("%s: the rewritten texture does not decode to the intended picture" % what)
    else:
        err = mean_error(got, expect)
        if err > LOSSY_MEAN:
            raise RuntimeError("%s: the rewritten texture decodes %.1f off the intended picture (limit %.0f)"
                               % (what, err, LOSSY_MEAN))
        if b.kind == "hd" and b.width >= 8 and b.height >= 8:
            mip = b.mip_pixels()[1]
            want_mip = croteam_tex.half(expect, b.width, b.height)[0]
            if mean_error(mip[0], want_mip) > LOSSY_MEAN:
                raise RuntimeError("%s: the first mip level does not match the picture" % what)
    return new


def dominant_clear(rgba):
    """The most common colour under alpha 0 (the game's transparent-pixel convention), (0, 0, 0) if none."""
    counts = {}
    for i in range(0, len(rgba), 4):
        if rgba[i + 3] == 0:
            c = (rgba[i], rgba[i + 1], rgba[i + 2])
            counts[c] = counts.get(c, 0) + 1
    return max(counts, key=counts.get) if counts else (0, 0, 0)


# ------------------------------------------------------------------ builders


class RoundSymbol:
    """A square button texture whose art is an oval: draw one of our symbols, round, on the oval's centre.

    `art`       a bundled picture (name relative to the package folder, "glyphs/ps/outline-blue-cross")
    `box`       the measured opaque box of the ORIGINAL art (alpha > 24), (x0, y0, x1, y1)
    `clear`     colour stored under alpha 0 in the original
    `diameter`  None = the box's short side (tune here after looking in the game)
    `fit`       "round" (a symbol in a square of `diameter`) | "box" (aspect-kept into the whole box: shoulders...)
    `clear`     (r, g, b) or "bleed" (HD: the colour of the nearest visible pixel)
    """

    def __init__(self, art, box, clear, diameter=None, fit="round"):
        self.art_name, self.box, self.diameter, self.fit = art, tuple(box), diameter, fit
        self.clear = clear if clear == "bleed" else tuple(clear)

    def art(self):
        return [self.art_name]

    def params(self):
        return ("RoundSymbol", self.art_name, self.box, self.clear, self.diameter, self.fit)

    def preflight(self, pictures):
        rgba, w, h = pictures(self.art_name)
        if len(rgba) != w * h * TEXTURE_BYTES or opaque_box(rgba, w, h) is None:
            raise ValueError("it has no visible pixel")

    def intended(self, entry, pictures):
        icon, iw, ih = pictures(self.art_name)
        return round_glyph(icon, iw, ih, entry["width"], entry["height"], self.box, self.clear, self.diameter, self.fit)

    def build(self, original, entry, style, pictures):
        want = self.intended(entry, pictures)
        lossy = croteam_tex.parse(original).fmt_name in ("dxt3", "dxt5")
        return check_reencoded(original, croteam_tex.reencode(original, want), want, entry["key"], lossy)


class WholePicture:
    """The whole texture replaced by a bundled RGBA picture of exactly its size."""

    def __init__(self, art, clear=(0, 0, 0)):
        self.art_name, self.clear = art, tuple(clear)

    def art(self):
        return [self.art_name]

    def params(self):
        return ("WholePicture", self.art_name, self.clear)

    def preflight(self, pictures):
        rgba, w, h = pictures(self.art_name)
        if len(rgba) != w * h * TEXTURE_BYTES:
            raise ValueError("it is not an RGBA picture")

    def intended(self, entry, pictures):
        rgba, w, h = pictures(self.art_name)
        if (w, h) != (entry["width"], entry["height"]):
            raise ValueError("it is %dx%d, the texture is %dx%d" % (w, h, entry["width"], entry["height"]))
        return normalise_clear(rgba, self.clear)

    def build(self, original, entry, style, pictures):
        want = self.intended(entry, pictures)
        lossy = croteam_tex.parse(original).fmt_name in ("dxt3", "dxt5")
        return check_reencoded(original, croteam_tex.reencode(original, want), want, entry["key"], lossy)


def _footprint_ok(level, lw, lh, bx, by, cells):
    """True when block (bx, by) of mip `level` lies inside (or one block around) a changed cell's footprint."""
    if lw < 8 or lh < 8:                                    # tiny levels are regenerated whole by the writer
        return True
    for x, y, w, h in cells:
        x0, x1 = (x >> level) // 4 - 1, ((x + w - 1) >> level) // 4 + 1
        y0, y1 = (y >> level) // 4 - 1, ((y + h - 1) >> level) // 4 + 1
        if x0 <= bx <= x1 and y0 <= by <= y1:
            return True
    return False


def untouched_outside(original, new, block, cells):
    """Every 4x4 DXT block of every mip level that differs between `original` and `new` (natural, unfiltered) lies in the
    footprint of one of `cells` = [(x, y, w, h)] (level 0 coordinates). Returns the number of blocks that changed."""
    a = croteam_tex.block_tex(original, block).surface()
    b = croteam_tex.block_tex(new, block).surface()
    if len(a) != len(b):
        raise RuntimeError("the atlas block changed length")
    pos, changed = 0, 0
    for level, (lw, lh) in enumerate(croteam_tex.levels(block.width, block.height)):
        nbx, nby = max(1, (lw + 3) // 4), max(1, (lh + 3) // 4)
        size = nbx * nby * 16
        if a[pos:pos + size] != b[pos:pos + size]:
            for i in range(nbx * nby):
                o = pos + i * 16
                if a[o:o + 16] != b[o:o + 16]:
                    changed += 1
                    if not _footprint_ok(level, lw, lh, i % nbx, i // nbx, cells):
                        raise RuntimeError("the atlas changed outside the redrawn cells (mip %d, block %d,%d)"
                                           % (level, i % nbx, i // nbx))
        pos += size
    return changed


class AtlasCells:
    """Cells of one pixel block of a multi-texture file (a CStaticAtlas .rsc) redrawn in place.

    `cells` = [(x, y, w, h, symbol)] in level-0 coordinates of block number `block`; `symbol` is a builder with
    `intended(entry, pictures)` (the same drawing the standalone texture gets). Written with the block-granular writer
    (croteam_tex.with_region): only the 4x4 blocks under a cell and their mip footprints are re-encoded, every other byte
    of the file is identical. Proven before it is returned: same length, bytes outside the block identical, every cell
    decodes to its picture, and no block outside the cells' footprints changed.
    """

    def __init__(self, block, cells):
        self.block, self.cells = block, [tuple(c) for c in cells]

    def art(self):
        out = []
        for c in self.cells:
            for n in c[4].art():
                if n not in out:
                    out.append(n)
        return out

    def params(self):
        return ("AtlasCells", self.block, tuple(c[:4] + (c[4].params(),) for c in self.cells))

    def preflight(self, pictures):
        for c in self.cells:
            c[4].preflight(pictures)

    def build(self, original, entry, style, pictures):
        blocks = croteam_tex.find_blocks(original)
        if len(blocks) <= self.block:
            raise RuntimeError("%s: the atlas has %d pixel blocks, expected block %d" % (entry["key"], len(blocks), self.block))
        blk = blocks[self.block]
        if (blk.width, blk.height, blk.fmt_name) != (entry["width"], entry["height"], entry.get("fmt", "dxt5")):
            raise RuntimeError("%s: block %d is %dx%d %s, not the pinned atlas" % (entry["key"], self.block, blk.width,
                                                                                    blk.height, blk.fmt_name))
        out, wants = original, []
        for x, y, w, h, symbol in self.cells:
            want = symbol.intended({"width": w, "height": h, "key": "%s@%d,%d" % (entry["key"], x, y)}, pictures)
            wants.append(want)
            out = croteam_tex.with_region(out, blk, x, y, want, w, h)
        if len(out) != len(original) or out[:blk.start] != original[:blk.start] \
                or out[blk.start + blk.size:] != original[blk.start + blk.size:]:
            raise RuntimeError("%s: the atlas changed outside its pixel block" % entry["key"])
        for (x, y, w, h, _), want in zip(self.cells, wants):
            got = croteam_tex.region_pixels(out, blk, x, y, w, h)
            if mean_error(got, want) > LOSSY_MEAN:
                raise RuntimeError("%s: cell %d,%d does not decode to the intended picture" % (entry["key"], x, y))
        untouched_outside(original, out, blk, [c[:4] for c in self.cells])
        return out


# ------------------------------------------------------------------ the profile


class OverlayGlyphProfile:
    kind = "overlay"
    overlay_level = True

    def __init__(self, appid, name, entries, builders, roles, native_style, mount=".", archive="zz_ButtonGlyphs.gro",
                 vehicle="gro", loose_root=".", load_order=(), notes="", revision=1, original_hint="", support="partial", styles=("ps",),
                 pictures=None):
        self.appid = appid
        self.name = name
        self.entries = entries
        self.builders = builders
        self.roles = roles
        self.native_style = native_style
        self.mount = mount
        self.archive = archive
        self.vehicle = vehicle
        self.load_order = [tuple(x) for x in load_order]    # [(archive name, bytes)] of the supported build (read-only probes)
        self.loose_root = loose_root      # where the loose vehicle writes the entries' virtual paths (the game's root)
        self.file = "/".join(p for p in (mount, archive) if p and p != ".")     # what the game folder gets
        self.revision = revision
        self.support = support
        self.notes = notes
        self.original_hint = original_hint
        self.patch_styles = tuple(styles)
        self.styles = {}
        self.textures = []
        self.pictures = pictures or PictureLoader(PACKAGE_DIR)
        self._memo = {}
        self._validate()

    def _validate(self):
        """The table is code, not user input: a mistake in it fails at import, not in a game folder."""
        if self.vehicle not in ("gro", "loose"):
            raise ValueError("unknown vehicle %r" % self.vehicle)
        if not self.archive.lower().endswith(".gro") or "/" in self.archive or "\\" in self.archive:
            raise ValueError("bad archive name %r" % self.archive)
        keys, paths = set(), set()
        for e in self.entries:
            parts = e["path"].split("/")
            if e["key"] in keys or os.path.isabs(e["path"]) or ".." in parts or "" in parts or "\\" in e["path"]:
                raise ValueError("%s: duplicate key or unsafe path %r" % (e["key"], e["path"]))
            if e["path"].lower() in paths:
                raise ValueError("%s: path %r is listed twice" % (e["key"], e["path"]))
            keys.add(e["key"])
            paths.add(e["path"].lower())
            if len(e["sha256"]) != 64 or e["size"] <= 0:
                raise ValueError("%s: bad pin" % e["key"])
            if e["key"] not in self.builders:
                raise ValueError("%s: no builder" % e["key"])
        if set(self.builders) - keys:
            raise ValueError("a builder without an entry")
        if not self.entries:
            raise ValueError("no entries")

    # ------------------------------------------------------------------ preflight

    def preflight(self):
        """Reload and validate every bundled picture BEFORE the first byte is written.

        Raises RuntimeError with a clear message, so a missing or damaged PNG fails the whole apply instead of
        stopping it halfway. Also forgets the memoised outputs, so the apply draws from what is on disk now.
        """
        self._memo.clear()
        getattr(self.pictures, "cache", {}).clear()
        for e in self.entries:
            b = self.builders[e["key"]]
            for name in b.art():
                try:
                    b.preflight(self.pictures)
                except Exception as exc:
                    raise RuntimeError("%s: bundled picture %r is missing or damaged (%s)" % (e["key"], name, exc))

    # ------------------------------------------------------------------ building

    def outputs(self, style, originals):
        """{entry path: replacement bytes} for `style`, each proven to decode to the intended picture."""
        if style not in self.patch_styles:
            raise ValueError("unknown style %r" % style)
        key = (style, self.revision)
        if key not in self._memo:
            out = {}
            for e in self.entries:
                original = originals[e["path"]]
                if len(original) != e["size"]:
                    raise ValueError("%s: wrong size" % e["key"])
                out[e["path"]] = self.builders[e["key"]].build(original, e, style, self.pictures)
            self._memo[key] = out
        return self._memo[key]

    # ------------------------------------------------------------------ fingerprint (cheap status)

    def fingerprint(self, style):
        """SHA-256 of everything the output of `style` is a function of: the profile's pins and parameters, the BYTES of
        the bundled pictures, the revision and the source of the code that draws and writes it. Equal fingerprints
        mean equal bytes, so a file whose hash was recorded for this fingerprint is current without rebuilding it.
        Raises OSError when a bundled picture cannot be read (then nothing can be proven current)."""
        h = hashlib.sha256()

        def add(x):
            h.update(repr(x).encode("utf-8") + b"\0")

        add(("overlay-fingerprint", 1, self.appid, self.revision, style, self.vehicle, self.mount, self.loose_root,
             self.archive))
        for e in self.entries:
            add((e["key"], e["path"], e["size"], e["sha256"]))
            b = self.builders[e["key"]]
            add(b.params())
            for name in b.art():
                with open(os.path.join(self.pictures.mask_dir, name + ".png"), "rb") as f:
                    h.update(hashlib.sha256(f.read()).digest())
        for mod in (overlay_patch, sys.modules[__name__], croteam_tex, image, gro):
            h.update(_source_digest(mod))
        return h.hexdigest()


_SOURCE = {}


def _source_digest(module):
    """SHA-256 of a module's source file (read once per process; a changed drawing or writer changes every fingerprint)."""
    path = module.__file__
    key = (path, os.stat(path).st_mtime_ns)
    if key not in _SOURCE:
        with open(path, "rb") as f:
            _SOURCE[key] = hashlib.sha256(f.read()).digest()
    return _SOURCE[key]
