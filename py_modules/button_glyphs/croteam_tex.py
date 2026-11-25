"""Croteam Serious Engine .tex textures: read the pixel block, write a new one, keep everything else.

Serious Sam HD: The First / Second Encounter (Serious Engine 3.5, Steam 41000 / 41010) and Serious
Sam 2 (Serious Engine 2, Steam 204340) ship their textures as `.tex` files inside `.gro` archives
(a .gro is a plain PKZIP, see gro.py). A .tex is a serialised object graph:

  "CTSEMETA" 0x1234ABCD (LE: cd ab 34 12) ... type tables, the "$Version: ..." stamp ...  <head>
  w, h, ...                                   (fields, see below)
  "STAR" uint32 size <size bytes of pixels>   <the pixel block>
  ... 64-68 bytes of trailer ending "EDOB" 0 "METAEND "                                  <tail>

The head and tail are carried through byte for byte (the HD version stamp differs between FE and
TSE, and the tail differs per pixel format), so `reencode(original, rgba)` takes the game's OWN
original file and swaps only the pixel block. Nothing outside the block, and the 4-byte size in
front of it, changes. No game bytes live in this repository.

== Serious Engine 3.5 (HD) ==  fields are the 16 bytes before "STAR":
    uint32 width, uint32 height, 4 opaque flag bytes (01 02 00 08 in almost all files), uint32 format
  format 3 and 5: 4 bytes/pixel, lanes R,G,B,A (straight alpha; hue evidence: HD B/X/Y match the
                  SS2 colours; 3 vs 5 is carried through, its meaning is unknown)
  format 6:       16 bits/pixel (not decoded here; parse works, pixel access raises)
  format 10:      DXT5        format 11: DXT3  (same 16 B blocks, 4x4)
  The block holds the FULL mip chain (down to 1x1, DXT levels padded to one 4x4 block), level 0
  first, rows top-down. The bytes are NOT stored as the GPU wants them: they are a bijective
  "shuffle + delta" filter (this is what made linear decodes look like noise):
    RGBA: 4 planes of N bytes (N = pixels over all levels). Plane k = byte k of every pixel in
          stream order; each plane is delta coded: stored[i] = (true[i] - true[i-1]) mod 256.
    DXT3/5 with B = total blocks over all levels, four sections laid out one after another:
          [ colour endpoint c0 : B x uint16 LE, delta coded mod 65536 ]
          [ colour endpoint c1 : B x uint16 LE, delta coded mod 65536 ]
          [ colour indices     : B x 4 bytes, plain ]
          [ alpha block        : B x 8 bytes (DXT5 a0 a1 + 6 index bytes / DXT3 explicit), plain ]
    A normal block is alpha8 + c0 + c1 + indices4. The filter is a bijection, so
    filter(unfilter(block)) == block for ANY bytes (tobytes() of an untouched parse is identical).
  The stored mips are close to a 2x2 box filter of the level above (see `half`; the game's own differ a little,
  see the end of this docstring): the engine does not rebuild them, so a rewritten texture must regenerate them
  or small renders show the old picture.

== Serious Engine 2 (SS2) ==  fields are the 24 bytes before "STAR":
    int32 0, int32 -1 (or 0), uint32 width, uint32 height, uint32 format (32/33/36), uint32 mips
  The block is a zlib stream (78 9c, level 6: recompressing the inflated bytes at level 6 gives back
  the original stream byte for byte in all 74 files checked). Supported: mips == 1 and the
  inflated size == w*h*4, raw B,G,R,A (straight alpha, rows top-down). Older layers store DXT with
  mips in the stream; they are refused (`TexError`) because a patch never needs them (a later .gro
  layer replaces them with raw BGRA). Only the size field in front of the zlib stream changes.

Traps: the first "STAR" is the pixel block, a second one sits in the trailer (never search for the
LAST one); the size of the pixel block must match width/height/format before it is believed;
`tobytes()` of an untouched parse must equal the input (tested on every sample).

== Several textures in one file (an atlas .rsc) and REGION writes ==
A CStaticAtlas .rsc (GlobalAtlas.rsc of Serious Sam HD, 11-12 MB) is ONE "CTSEMETA" object graph with
several HD texture blocks, each laid out like a .tex: `w, h, 4 flag bytes, format, "STAR", size, pixels`
(FE: 256x128 + 512x512 + 4096x2048 DXT5; TSE: 512x512 + 1024x512 + 4096x2048), and after each block a
`"STAR" 0` stub with fields 81,81 (a decoy: size 0 never matches a surface). The flag bytes differ per
block (0x0701 here, 0x0301 on the big one): carried through, never interpreted.
  find_blocks(data)            every pixel block, in file order (`Block`: offset of "STAR", start, size, w, h, fmt)
  get_block(data, index=..) / (w=, h=, fmt_name=)   exactly one block or TexError (never "the first")
  block_tex(data, block)       a `Tex` for that block whose head/tail are the REST OF THE FILE, so
                               `tex.tobytes()` is the whole .rsc again (the profile builds the new .rsc from it)
  region_pixels / with_region  read or replace a rectangle of one block, file-level wrappers of the Tex methods
`Tex.with_region(x, y, rgba, w, h)` re-encodes ONLY the 4x4 blocks inside the rectangle at level 0 (x, y, w, h
multiples of 4, else TexError) and, per mip level, only the blocks that cover the pixels whose 2^L footprint
touches the rectangle (pixels there come from the same premultiplied 2x2 box as `half`, parents = the new exact
pixels where they exist and the DECODED ORIGINAL elsewhere; a covering block that is only partly inside the
rectangle is re-encoded from decoded-original + new pixels). Every other block stays identical in the natural
surface. Traps: (1) the stored DXT block is delta coded across blocks, so changing block i also changes the
stored 2-byte delta of block i+1 (c0 and c1 sections): "changed stored bytes" is a little larger than "changed
blocks"; compare in natural (unfiltered) space to count blocks. (2) a mip level smaller than 4 in one axis, or an
odd parent, is regenerated whole (the box falls back to `image.resize`, same as `with_pixels`). (3) reading a
rectangle decodes only the covering blocks: never decode the 4096x2048 block (11 MB) in pure Python.
Limits: 16-bit (format 6) and SE2 with mips are refused; SE2 raw BGRA is patched through pixels() (one level).
Encoder (`encode_block`, used by `with_pixels` and `with_region`): colour = range fit along the principal axis
(`_color_block`), DXT5 alpha = best of the two modes (`_alpha_modes`). `image._encode_color` has a latent bug the
real atlas did not trigger but a synthetic test did: its power iteration starts at (1,1,1), which is orthogonal to
the axis of two saturated colours (red/green, magenta/yellow), so such a block collapses to ONE colour. Not fixed
in image.py (other profiles' output would change); `croteam_tex` does not call it for the colour part.
Real data (FE/TSE GlobalAtlas.rsc, white icons shaped by alpha): decoded original blocks re-encode with colour
error 0 on visible pixels, alpha exact in 99.4% of blocks (worst 14); the game's own mips differ from a 2x2 box
of its level 0 (mean alpha diff 0.3-4.8, worst 40), so `with_region` regenerates the covered mip blocks.
"""
import collections
import struct
import zlib

from . import image

MAGIC = b"CTSEMETA\xcd\xab\x34\x12"

# HD pixel formats (uint32 before the pixel block) -> (name, bytes per pixel or None for DXT)
HD_FORMATS = {3: "rgba8", 5: "rgba8", 6: "bits16", 10: "dxt5", 11: "dxt3"}
SE2_ZLIB_LEVEL = 6
_MAX_DIM = 16384


class TexError(ValueError):
    """The file is not a layout this module has proven; touch nothing."""


# --------------------------------------------------------------------------- geometry


def levels(w, h):
    """[(w, h)] of the full mip chain, level 0 first, down to 1x1."""
    out = [(w, h)]
    while (w, h) != (1, 1):
        w, h = max(1, w // 2), max(1, h // 2)
        out.append((w, h))
    return out


def _blocks(w, h):
    return max(1, (w + 3) // 4) * max(1, (h + 3) // 4)


def surface_size(w, h, name):
    """Bytes of the whole mip chain for a format name ('rgba8', 'bits16', 'dxt3', 'dxt5')."""
    if name == "rgba8":
        return sum(a * b * 4 for a, b in levels(w, h))
    if name == "bits16":
        return sum(a * b * 2 for a, b in levels(w, h))
    if name in ("dxt3", "dxt5"):
        return sum(_blocks(a, b) * 16 for a, b in levels(w, h))
    raise TexError("unknown format %r" % name)


# --------------------------------------------------------------------------- the HD filter


def unfilter(body, name):
    """Stored HD pixel block -> natural surface (RGBA interleaved, or 16-byte DXT blocks)."""
    if name == "rgba8":
        if len(body) % 4:
            raise TexError("rgba8 block length %d not a multiple of 4" % len(body))
        n = len(body) // 4
        out = bytearray(len(body))
        for k in range(4):
            acc = 0
            lane = bytearray(n)
            for i, v in enumerate(body[k * n:(k + 1) * n]):
                acc = (acc + v) & 0xFF
                lane[i] = acc
            out[k::4] = lane
        return bytes(out)
    if name in ("dxt3", "dxt5"):
        if len(body) % 16:
            raise TexError("DXT block length %d not a multiple of 16" % len(body))
        n = len(body) // 16
        c0 = _cum16(body[0:2 * n])
        c1 = _cum16(body[2 * n:4 * n])
        out = bytearray(len(body))
        for i in range(n):
            o = i * 16
            out[o:o + 8] = body[8 * n + 8 * i:8 * n + 8 * i + 8]
            struct.pack_into("<HH", out, o + 8, c0[i], c1[i])
            out[o + 12:o + 16] = body[4 * n + 4 * i:4 * n + 4 * i + 4]
        return bytes(out)
    raise TexError("format %r has no filter here" % name)


def filter_surface(natural, name):
    """Natural surface -> stored HD pixel block (exact inverse of `unfilter`)."""
    if name == "rgba8":
        n = len(natural) // 4
        out = bytearray(len(natural))
        for k in range(4):
            prev = 0
            lane = natural[k::4]
            dst = bytearray(n)
            for i, v in enumerate(lane):
                dst[i] = (v - prev) & 0xFF
                prev = v
            out[k * n:(k + 1) * n] = dst
        return bytes(out)
    if name in ("dxt3", "dxt5"):
        n = len(natural) // 16
        out = bytearray(len(natural))
        c0 = [struct.unpack_from("<H", natural, i * 16 + 8)[0] for i in range(n)]
        c1 = [struct.unpack_from("<H", natural, i * 16 + 10)[0] for i in range(n)]
        out[0:2 * n] = _delta16(c0)
        out[2 * n:4 * n] = _delta16(c1)
        for i in range(n):
            o = i * 16
            out[4 * n + 4 * i:4 * n + 4 * i + 4] = natural[o + 12:o + 16]
            out[8 * n + 8 * i:8 * n + 8 * i + 8] = natural[o:o + 8]
        return bytes(out)
    raise TexError("format %r has no filter here" % name)


def _cum16(raw):
    vals = struct.unpack("<%dH" % (len(raw) // 2), raw)
    acc, out = 0, []
    for v in vals:
        acc = (acc + v) & 0xFFFF
        out.append(acc)
    return out


def _delta16(vals):
    prev, out = 0, bytearray()
    for v in vals:
        out += struct.pack("<H", (v - prev) & 0xFFFF)
        prev = v
    return bytes(out)


# --------------------------------------------------------------------------- parse / serialise


class Tex:
    """A parsed .tex: head, pixel block (as stored) and tail are kept verbatim.

    kind      'hd' or 'se2'
    width, height, fmt (uint32 from the file), fmt_name ('rgba8' | 'bits16' | 'dxt3' | 'dxt5' | 'bgra8')
    head      bytes up to and including the "STAR" tag
    stored    the pixel block exactly as in the file (HD: filtered; SE2: the zlib stream)
    tail      bytes after the pixel block
    """

    def __init__(self, kind, width, height, fmt, fmt_name, head, stored, tail):
        self.kind, self.width, self.height = kind, width, height
        self.fmt, self.fmt_name = fmt, fmt_name
        self.head, self.stored, self.tail = head, stored, tail

    def tobytes(self):
        return self.head + struct.pack("<I", len(self.stored)) + self.stored + self.tail

    @property
    def supported(self):
        """True when pixels() / with_pixels() work for this texture."""
        return self.fmt_name in ("rgba8", "bgra8", "dxt3", "dxt5")

    def surface(self):
        """The natural surface: every mip level, level 0 first (see module docstring)."""
        if self.kind == "hd":
            return unfilter(self.stored, self.fmt_name)
        return zlib.decompress(self.stored)

    def pixels(self):
        """Level 0 as straight RGBA, rows top-down: (rgba bytes, width, height)."""
        if not self.supported:
            raise TexError("format %s (%s) cannot be decoded" % (self.fmt, self.fmt_name))
        w, h = self.width, self.height
        surf = self.surface()
        if self.fmt_name == "rgba8":
            return surf[:w * h * 4], w, h
        if self.fmt_name == "bgra8":
            return _swap_rb(surf[:w * h * 4]), w, h
        return image.decode(surf[:_blocks(w, h) * 16], w, h, self.fmt_name.upper()), w, h

    def mip_pixels(self):
        """Every level as straight RGBA: [(rgba, w, h)] (HD formats and SE2 level 0)."""
        if not self.supported:
            raise TexError("format %s (%s) cannot be decoded" % (self.fmt, self.fmt_name))
        surf, out, pos = self.surface(), [], 0
        lv = levels(self.width, self.height) if self.kind == "hd" else [(self.width, self.height)]
        for lw, lh in lv:
            if self.fmt_name in ("rgba8", "bgra8"):
                raw = surf[pos:pos + lw * lh * 4]
                pos += lw * lh * 4
                out.append((raw if self.fmt_name == "rgba8" else _swap_rb(raw), lw, lh))
            else:
                size = _blocks(lw, lh) * 16
                out.append((image.decode(surf[pos:pos + size], lw, lh, self.fmt_name.upper()), lw, lh))
                pos += size
        return out

    def with_pixels(self, rgba):
        """A new Tex with level 0 = rgba (exactly width*height*4 bytes); mips are regenerated."""
        if not self.supported:
            raise TexError("format %s (%s) cannot be written" % (self.fmt, self.fmt_name))
        w, h = self.width, self.height
        if len(rgba) != w * h * 4:
            raise TexError("need %d RGBA bytes for %dx%d, got %d" % (w * h * 4, w, h, len(rgba)))
        if self.kind == "se2":
            raw = _swap_rb(rgba)
            stored = zlib.compress(raw, SE2_ZLIB_LEVEL)
        else:
            chain = mip_chain(bytes(rgba), w, h)
            if self.fmt_name == "rgba8":
                natural = b"".join(level for level, _, _ in chain)
            else:
                natural = b"".join(_encode_dxt(level, lw, lh, self.fmt_name.upper()) for level, lw, lh in chain)
            stored = filter_surface(natural, self.fmt_name)
            if len(stored) != len(self.stored):
                raise TexError("rewritten block is %d bytes, original %d" % (len(stored), len(self.stored)))
        return Tex(self.kind, w, h, self.fmt, self.fmt_name, self.head, stored, self.tail)

    # ----- rectangles (region read / write); see the module docstring

    def _region_ok(self):
        if self.kind == "se2":
            if self.fmt_name != "bgra8":
                raise TexError("this SE2 texture (%s) cannot be read by region" % self.fmt_name)
        elif self.fmt_name not in ("rgba8", "dxt3", "dxt5"):
            raise TexError("format %s (%s) cannot be read or written by region" % (self.fmt, self.fmt_name))

    def _check_rect(self, x, y, w, h, lw, lh, aligned):
        for v in (x, y, w, h):
            if not isinstance(v, int) or isinstance(v, bool):
                raise TexError("rectangle values must be integers")
        if w < 1 or h < 1 or x < 0 or y < 0 or x + w > lw or y + h > lh:
            raise TexError("rectangle %d,%d %dx%d is outside the %dx%d level" % (x, y, w, h, lw, lh))
        if aligned and (x % 4 or y % 4 or w % 4 or h % 4):
            raise TexError("DXT rectangle %d,%d %dx%d must be a multiple of 4 (whole 4x4 blocks)" % (x, y, w, h))

    def region_pixels(self, x, y, w, h, level=0):
        """Straight RGBA bytes (w*h*4, rows top-down) of a rectangle of mip `level` (0 = full size).

        Any rectangle works for reading (only the covering 4x4 blocks are decoded)."""
        self._region_ok()
        lv = levels(self.width, self.height) if self.kind == "hd" else [(self.width, self.height)]
        if not 0 <= level < len(lv):
            raise TexError("no mip level %r (%d levels)" % (level, len(lv)))
        self._check_rect(x, y, w, h, lv[level][0], lv[level][1], False)
        if self.kind == "se2":
            full = self.pixels()[0]
            return b"".join(full[((y + j) * self.width + x) * 4:((y + j) * self.width + x + w) * 4] for j in range(h))
        return _read_rect(self.surface(), self.width, self.height, self.fmt_name, level, (x, y, x + w, y + h), None)

    def with_region(self, x, y, rgba, w, h):
        """A new Tex where the rectangle (x, y, w, h) of level 0 is `rgba` (straight RGBA, w*h*4 bytes).

        DXT3/5: x, y, w, h must be multiples of 4; only the blocks inside the rectangle are re-encoded at
        level 0, and per mip level only the blocks covering the touched pixels (see the module docstring).
        RGBA8 HD: any rectangle, the touched pixels of every level are rewritten. Head, tail and the stored
        length are unchanged."""
        self._region_ok()
        rgba = bytes(rgba)
        self._check_rect(x, y, w, h, self.width, self.height, self.fmt_name in ("dxt3", "dxt5"))
        if len(rgba) != w * h * 4:
            raise TexError("need %d RGBA bytes for %dx%d, got %d" % (w * h * 4, w, h, len(rgba)))
        if self.kind == "se2":
            full = bytearray(self.pixels()[0])
            for j in range(h):
                o = ((y + j) * self.width + x) * 4
                full[o:o + w * 4] = rgba[j * w * 4:(j + 1) * w * 4]
            return self.with_pixels(bytes(full))
        orig = self.surface()
        out = bytearray(orig)
        rect, pix = (x, y, x + w, y + h), rgba
        for lvl, (lw, lh) in enumerate(levels(self.width, self.height)):
            if lvl:
                plw, plh = levels(self.width, self.height)[lvl - 1]
                if plw % 2 or plh % 2:   # odd or 1-wide parent: `half` falls back to a resize, so redo the whole level
                    rect, src = (0, 0, lw, lh), (0, 0, plw, plh)
                else:
                    rect = (rect[0] // 2, rect[1] // 2, min(lw, (rect[2] + 1) // 2), min(lh, (rect[3] + 1) // 2))
                    src = (2 * rect[0], 2 * rect[1], 2 * rect[2], 2 * rect[3])
                parent = _read_rect(orig, self.width, self.height, self.fmt_name, lvl - 1, src, (prev_rect, pix))
                pix, nw, nh = half(parent, src[2] - src[0], src[3] - src[1])
                if (nw, nh) != (rect[2] - rect[0], rect[3] - rect[1]):
                    raise TexError("mip rectangle mismatch at level %d" % lvl)
            _write_rect(out, orig, self.width, self.height, self.fmt_name, lvl, rect, pix)
            prev_rect = rect
        stored = filter_surface(bytes(out), self.fmt_name)
        if len(stored) != len(self.stored):
            raise TexError("rewritten block is %d bytes, original %d" % (len(stored), len(self.stored)))
        return Tex(self.kind, self.width, self.height, self.fmt, self.fmt_name, self.head, stored, self.tail)


def _level_at(w, h, name, lvl):
    """(byte offset in the natural surface, level width, level height) of mip `lvl`."""
    pos = 0
    lv = levels(w, h)
    for lw, lh in lv[:lvl]:
        pos += lw * lh * 4 if name == "rgba8" else _blocks(lw, lh) * 16
    return pos, lv[lvl][0], lv[lvl][1]


def _aligned(rect, lw, lh):
    """The 4-aligned rectangle of whole DXT blocks covering `rect` (it may stick out past a small level)."""
    x0, y0, x1, y1 = rect
    return (x0 // 4 * 4, y0 // 4 * 4, min((x1 + 3) // 4, _bw(lw)) * 4, min((y1 + 3) // 4, _bw(lh)) * 4)


def _bw(n):
    return max(1, (n + 3) // 4)


def _decode_blocks(surf, off, lw, lh, name, ar):
    """RGBA of the aligned rectangle `ar` of a DXT level starting at `off` (only those blocks are decoded)."""
    nbx = _bw(lw)
    bx0, by0, bx1, by1 = ar[0] // 4, ar[1] // 4, ar[2] // 4, ar[3] // 4
    raw = b"".join(surf[off + (by * nbx + bx0) * 16:off + (by * nbx + bx1) * 16] for by in range(by0, by1))
    return image.decode(raw, ar[2] - ar[0], ar[3] - ar[1], name.upper())


def _overlay(buf, bx, by, bw, new, rect):
    """Copy `new` (RGBA of `rect`) into `buf` whose top-left is (bx, by) and width bw; only the overlap."""
    nx0, ny0, nx1, ny1 = rect
    x0, x1 = max(nx0, bx), min(nx1, bx + bw)
    if x0 >= x1:
        return
    nh = len(buf) // (bw * 4)
    for yy in range(max(ny0, by), min(ny1, by + nh)):
        s = ((yy - ny0) * (nx1 - nx0) + (x0 - nx0)) * 4
        d = ((yy - by) * bw + (x0 - bx)) * 4
        buf[d:d + (x1 - x0) * 4] = new[s:s + (x1 - x0) * 4]


def _read_rect(surf, w, h, name, lvl, rect, exact):
    """RGBA of `rect` (x0, y0, x1, y1) at mip `lvl`: decoded original, with `exact` = (rect, rgba) laid over it."""
    off, lw, lh = _level_at(w, h, name, lvl)
    x0, y0, x1, y1 = rect
    if name == "rgba8":
        buf = bytearray(b"".join(surf[off + (yy * lw + x0) * 4:off + (yy * lw + x1) * 4] for yy in range(y0, y1)))
        bx, by, bw = x0, y0, x1 - x0
    else:
        ar = _aligned(rect, lw, lh)
        buf = bytearray(_decode_blocks(surf, off, lw, lh, name, ar))
        bx, by, bw = ar[0], ar[1], ar[2] - ar[0]
    if exact is not None:
        _overlay(buf, bx, by, bw, exact[1], exact[0])
    if name == "rgba8":
        return bytes(buf)
    return b"".join(bytes(buf[((yy - by) * bw + (x0 - bx)) * 4:((yy - by) * bw + (x1 - bx)) * 4]) for yy in range(y0, y1))


def _alpha_modes(alphas):
    """DXT5 alpha block (8 bytes): the better of the two modes, by squared error.

    Mode 8 (a0 > a1): 6 interpolants between the extremes. Mode 6 (a0 <= a1): 4 interpolants between the
    extremes of the values (the game's icons use a1 = 255 there), plus exact 0 and 255; the game's own atlas
    uses it in 97% of its blocks; `image._encode_alpha5` (mode 8 only) re-encodes them with errors up to 19,
    this one reproduces 99.4% of them exactly (worst 14: the game's endpoints were fitted, not the extremes)."""
    lo, hi = min(alphas), max(alphas)
    if lo == hi:   # one flat value: a0 = a1 = value, every index 0 (what the game's own flat blocks hold)
        return bytes([lo, lo]) + b"\0" * 6
    best, cands = None, []
    cands.append((hi, lo, [hi, lo] + [((6 - i) * hi + (i + 1) * lo) // 7 for i in range(6)]))
    mid = [a for a in alphas if 0 < a < 255]
    nz = [a for a in alphas if a > 0]
    lows = {min(mid) if mid else 0, min(nz) if nz else 0, lo}
    highs = {max(mid) if mid else 0, hi}
    for a0 in sorted(lows):
        for a1 in sorted(highs):
            if a0 <= a1:
                cands.append((a0, a1, [a0, a1] + [((4 - i) * a0 + (i + 1) * a1) // 5 for i in range(4)] + [0, 255]))
    for a0, a1, table in cands:
        bits, err = 0, 0
        for i, a in enumerate(alphas):
            k = min(range(8), key=lambda j: abs(a - table[j]))
            bits |= k << (3 * i)
            err += (a - table[k]) ** 2
        if best is None or err < best[0]:
            best = (err, bytes([a0, a1]) + bits.to_bytes(6, "little"))
    return best[1]


def _color_block(px):
    """8-byte colour part (4-colour mode, c0 > c1): range fit along the principal colour axis of the visible pixels.

    `image._encode_color` starts its power iteration at (1,1,1), which can be ORTHOGONAL to the axis of two
    saturated colours (red/green, red/blue, magenta/yellow: the product is the zero vector) and then collapses
    the block to a single colour. Here the start is the deviation of the pixel farthest from the mean, for which
    (cov * d) . d = sum(((p - mean) . d)^2) > 0 whenever the block has any spread, so it never degenerates."""
    vis = [p for p in px if p[3] >= 16] or px
    n = len(vis)
    mean = [sum(p[k] for p in vis) / n for k in range(3)]
    cov = [[sum((p[i] - mean[i]) * (p[j] - mean[j]) for p in vis) for j in range(3)] for i in range(3)]
    far = max(vis, key=lambda p: sum((p[k] - mean[k]) ** 2 for k in range(3)))
    axis = [far[k] - mean[k] for k in range(3)]
    if not any(axis):
        axis = [1.0, 1.0, 1.0]
    for _ in range(4):
        nxt = [sum(cov[i][j] * axis[j] for j in range(3)) for i in range(3)]
        norm = max(abs(v) for v in nxt)
        if norm == 0:
            break
        axis = [v / norm for v in nxt]
    proj = [sum((p[k] - mean[k]) * axis[k] for k in range(3)) for p in vis]
    c0, c1 = image._to565(*vis[proj.index(max(proj))][:3]), image._to565(*vis[proj.index(min(proj))][:3])
    if c0 < c1:
        c0, c1 = c1, c0
    if c0 == c1:                      # keep the 4-colour mode (c0 > c1)
        if c0 == 0:
            c0 = 1
        else:
            c1 = c0 - 1
    pal = image._palette(c0, c1, True)
    idx = 0
    for i, p in enumerate(px):
        best = min(range(4), key=lambda k: (p[0] - pal[k][0]) ** 2 + (p[1] - pal[k][1]) ** 2 + (p[2] - pal[k][2]) ** 2)
        idx |= best << (2 * i)
    return struct.pack("<HHI", c0, c1, idx)


def encode_block(px, fmt):
    """One 4x4 block (16 RGBA tuples, row-major) as DXT3/DXT5.

    DXT5: `_alpha_modes` + `_color_block` (see both). DXT3: `image.encode_block` unchanged apart from its colour
    part, which also comes from `_color_block`."""
    if fmt == "DXT5":
        return _alpha_modes([p[3] for p in px]) + _color_block(px)
    if fmt == "DXT3":
        bits = 0
        for i, p in enumerate(px):
            bits |= ((p[3] * 15 + 127) // 255) << (4 * i)
        return bits.to_bytes(8, "little") + _color_block(px)
    return image.encode_block(px, fmt)


def _write_rect(out, orig, w, h, name, lvl, rect, pix):
    """Put `pix` (RGBA of `rect`) into level `lvl` of the natural surface `out` (DXT: re-encode the covering blocks)."""
    off, lw, lh = _level_at(w, h, name, lvl)
    x0, y0, x1, y1 = rect
    if name == "rgba8":
        for yy in range(y0, y1):
            o = off + (yy * lw + x0) * 4
            out[o:o + (x1 - x0) * 4] = pix[(yy - y0) * (x1 - x0) * 4:(yy - y0 + 1) * (x1 - x0) * 4]
        return
    ar = _aligned(rect, lw, lh)
    bw = ar[2] - ar[0]
    if ar == rect:
        buf = bytearray(pix)
    else:   # a covering block only partly inside the rectangle: decoded original + the new pixels
        buf = bytearray(_decode_blocks(orig, off, lw, lh, name, ar))
        _overlay(buf, ar[0], ar[1], bw, pix, rect)
    fmt, nbx = name.upper(), _bw(lw)
    for by in range(ar[1] // 4, ar[3] // 4):
        for bx in range(ar[0] // 4, ar[2] // 4):
            px = []
            for j in range(4):
                sy = min(lh - 1, by * 4 + j) - ar[1]
                for i in range(4):
                    o = (sy * bw + min(lw - 1, bx * 4 + i) - ar[0]) * 4
                    px.append(tuple(buf[o:o + 4]))
            d = off + (by * nbx + bx) * 16
            out[d:d + 16] = encode_block(px, fmt)


def region_pixels(data, block, x, y, w, h, level=0):
    """RGBA of a rectangle of one block of a multi-texture file (see `find_blocks`)."""
    return block_tex(data, block).region_pixels(x, y, w, h, level)


def with_region(data, block, x, y, rgba, w, h):
    """The whole file with the rectangle of `block` replaced; same length, every byte outside the block identical."""
    out = block_tex(data, block).with_region(x, y, rgba, w, h).tobytes()
    if len(out) != len(data):
        raise TexError("region write changed the file length")
    return out


def _swap_rb(buf):
    out = bytearray(buf)
    out[0::4], out[2::4] = buf[2::4], buf[0::4]
    return bytes(out)


def parse(data):
    """Parse a .tex file. Raises TexError unless the layout is one this module has proven."""
    data = bytes(data)
    if data[:12] != MAGIC:
        raise TexError("not a CTSEMETA file")
    pos = data.find(b"STAR")
    while pos >= 0:
        if pos + 8 <= len(data):
            size = struct.unpack_from("<I", data, pos + 4)[0]
            end = pos + 8 + size
            if end <= len(data):
                if pos >= 16:
                    hit = _try_hd(data, pos, size)
                    if hit:
                        return hit
                if pos >= 24 and data[pos + 8:pos + 9] == b"\x78":
                    hit = _try_se2(data, pos, size)
                    if hit:
                        return hit
        pos = data.find(b"STAR", pos + 1)
    raise TexError("no pixel block matching a known Serious Engine layout")


def _hd_probe(data, pos, size):
    """(w, h, fmt, name) when the 16 bytes before the "STAR" at `pos` describe a block of exactly `size` bytes."""
    w, h, _flags, fmt = struct.unpack_from("<IIII", data, pos - 16)
    name = HD_FORMATS.get(fmt)
    if name is None or not (1 <= w <= _MAX_DIM and 1 <= h <= _MAX_DIM):
        return None
    if surface_size(w, h, name) != size:
        return None
    return w, h, fmt, name


def _try_hd(data, pos, size):
    hit = _hd_probe(data, pos, size)
    if hit is None:
        return None
    w, h, fmt, name = hit
    return Tex("hd", w, h, fmt, name, data[:pos + 4], data[pos + 8:pos + 8 + size], data[pos + 8 + size:])


# --------------------------------------------------------------------------- several blocks in one file

Block = collections.namedtuple("Block", "index offset start size width height fmt fmt_name")
Block.__doc__ = """One HD pixel block inside a file. offset = position of its "STAR" tag, start = first pixel byte
(offset + 8), size = bytes of the stored block; fmt is the uint32 from the file, fmt_name its name."""


def find_blocks(data):
    """Every HD pixel block of a CTSEMETA file, in file order (an atlas .rsc has several, a .tex one).

    A "STAR" counts only when the 16 bytes before it (w, h, flags, format) and its size field describe a
    surface of exactly that many bytes; the search resumes AFTER an accepted block, so bytes inside a
    block can never be taken for the next one."""
    if data[:12] != MAGIC:
        raise TexError("not a CTSEMETA file")
    out, pos = [], data.find(b"STAR")
    while pos >= 0:
        if pos >= 16 and pos + 8 <= len(data):
            size = struct.unpack_from("<I", data, pos + 4)[0]
            hit = _hd_probe(data, pos, size) if pos + 8 + size <= len(data) else None
            if hit:
                w, h, fmt, name = hit
                out.append(Block(len(out), pos, pos + 8, size, w, h, fmt, name))
                pos = data.find(b"STAR", pos + 8 + size)
                continue
        pos = data.find(b"STAR", pos + 1)
    return out


def get_block(data, index=None, w=None, h=None, fmt_name=None):
    """The one block selected by `index`, or by whichever of (w, h, fmt_name) are given. TexError if the
    selection matches none or more than one block (an atlas can hold two textures of one size)."""
    blocks = find_blocks(data)
    if index is not None:
        if not 0 <= index < len(blocks):
            raise TexError("block index %r out of range (%d blocks)" % (index, len(blocks)))
        return blocks[index]
    hits = [b for b in blocks if (w is None or b.width == w) and (h is None or b.height == h)
            and (fmt_name is None or b.fmt_name == fmt_name)]
    if len(hits) != 1:
        raise TexError("%d blocks match w=%r h=%r fmt=%r (of %d)" % (len(hits), w, h, fmt_name, len(blocks)))
    return hits[0]


def block_tex(data, block):
    """A Tex for `block` of `data`: head/tail are the rest of the FILE, so tobytes() gives the whole file back."""
    data = bytes(data)
    end = block.start + block.size
    if (data[block.offset:block.offset + 4] != b"STAR" or end > len(data)
            or struct.unpack_from("<I", data, block.offset + 4)[0] != block.size):
        raise TexError("block does not belong to this data")
    return Tex("hd", block.width, block.height, block.fmt, block.fmt_name,
               data[:block.offset + 4], data[block.start:end], data[end:])


def _try_se2(data, pos, size):
    a, b, w, h, fmt, mips = struct.unpack_from("<iiIIII", data, pos - 24)
    if a != 0 or b not in (0, -1) or not (1 <= w <= _MAX_DIM and 1 <= h <= _MAX_DIM):
        return None
    stored = data[pos + 8:pos + 8 + size]
    try:
        raw = zlib.decompress(stored)
    except zlib.error:
        return None
    name = "bgra8" if (mips == 1 and len(raw) == w * h * 4) else "unsupported"
    return Tex("se2", w, h, fmt, name, data[:pos + 4], stored, data[pos + 8 + size:])


# --------------------------------------------------------------------------- mips and DXT encode


def half(rgba, w, h):
    """2x2 box filter in premultiplied space (the engine's mip convention, see tests). Returns (rgba, w2, h2)."""
    nw, nh = max(1, w // 2), max(1, h // 2)
    if w % 2 or h % 2 or w == 1 or h == 1:
        return image.resize(rgba, w, h, nw, nh), nw, nh
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        r0, r1 = (2 * y) * w * 4, (2 * y + 1) * w * 4
        for x in range(nw):
            acc = [0, 0, 0, 0]
            asum = 0
            for o in (r0 + 8 * x, r0 + 8 * x + 4, r1 + 8 * x, r1 + 8 * x + 4):
                a = rgba[o + 3]
                asum += a
                acc[0] += rgba[o] * a
                acc[1] += rgba[o + 1] * a
                acc[2] += rgba[o + 2] * a
            d = (y * nw + x) * 4
            if asum:
                out[d] = min(255, (acc[0] + asum // 2) // asum)
                out[d + 1] = min(255, (acc[1] + asum // 2) // asum)
                out[d + 2] = min(255, (acc[2] + asum // 2) // asum)
                out[d + 3] = (asum + 2) // 4
            else:  # fully transparent: keep the colour under the alpha (plain mean), so a bled edge colour survives
                for k in range(3):
                    out[d + k] = (sum(rgba[o + k] for o in (r0 + 8 * x, r0 + 8 * x + 4, r1 + 8 * x, r1 + 8 * x + 4)) + 2) // 4
    return bytes(out), nw, nh


def mip_chain(rgba, w, h):
    """[(rgba, w, h)] from level 0 down to 1x1, each level made from the one above."""
    out = [(rgba, w, h)]
    while (w, h) != (1, 1):
        rgba, w, h = half(rgba, w, h)
        out.append((rgba, w, h))
    return out


def _encode_dxt(rgba, w, h, fmt):
    out = bytearray()
    for by in range(max(1, (h + 3) // 4)):
        for bx in range(max(1, (w + 3) // 4)):
            px = []
            for y in range(4):
                sy = min(h - 1, by * 4 + y)
                for x in range(4):
                    sx = min(w - 1, bx * 4 + x)
                    o = (sy * w + sx) * 4
                    px.append(tuple(rgba[o:o + 4]))
            out += encode_block(px, fmt)
    return bytes(out)


# --------------------------------------------------------------------------- the one call a profile uses


def reencode(original, rgba):
    """The game's own .tex with level 0 replaced by `rgba` (straight RGBA, same width x height).

    Head, trailer, flags and format come from `original`; mips are regenerated. Raises TexError if
    the layout is not a proven one or the pixel count does not match."""
    return parse(original).with_pixels(rgba).tobytes()


def decode(original):
    """Level 0 of a .tex file as (straight RGBA bytes, width, height)."""
    return parse(original).pixels()
