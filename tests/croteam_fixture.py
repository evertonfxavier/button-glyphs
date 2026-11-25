"""Synthetic Croteam .tex builder: the executable spec of the layout in croteam_tex.py.

It is written independently of the module (plain loops, its own filter) so a test compares two
implementations of the same description. The head and tail are invented filler with the real SHAPE:
magic, a version stamp, w/h/flags/format in front of "STAR" + size, a trailer that ends with a second
"STAR" and "EDOB" 0 "METAEND ". No game bytes.
"""
import struct
import zlib

MAGIC = b"CTSEMETA\xcd\xab\x34\x12"
TAIL_HD_DXT = (b"\x50\0\0\0\x50\0\0\0" + b"\0" * 8 + b"STAR" + b"\0" * 4 + b"\xff\xff\xff\xff" + b"\0\0\x80\x3f"
               + b"\0" * 8 + b"EDOB\0\0\0\0METAEND ")
TAIL_HD_RGBA = TAIL_HD_DXT.replace(b"\x50", b"\x51", 2)
TAIL_SE2 = b"\x51\0\0\0\x51\0\0\0\0\0\0\0EDOB\0\0\0\0METAEND "


def head(stamp, decoy=False):
    # decoy: a "STAR" with an absurd size earlier in the file must not fool the locator
    filler = b"INFO\1\0\0\0" + (b"STAR\xff\xff\xff\x7f" if decoy else b"") + b"\0" * 32
    return MAGIC + struct.pack("<II", 10, len(stamp)) + stamp + filler


def mips(w, h):
    out = [(w, h)]
    while (w, h) != (1, 1):
        w, h = max(1, w // 2), max(1, h // 2)
        out.append((w, h))
    return out


def hd_rgba_block(natural_levels):
    """natural_levels: list of RGBA byte strings (level 0 first) -> stored block (4 planes, delta each)."""
    stream = b"".join(natural_levels)
    n = len(stream) // 4
    out = bytearray()
    for k in range(4):
        prev = 0
        for i in range(n):
            v = stream[i * 4 + k]
            out.append((v - prev) & 255)
            prev = v
    return bytes(out)


def hd_dxt_block(blocks):
    """blocks: list of 16-byte DXT blocks (alpha8 + c0 c1 idx4), all levels -> stored sections."""
    n = len(blocks)
    c0 = [struct.unpack("<H", b[8:10])[0] for b in blocks]
    c1 = [struct.unpack("<H", b[10:12])[0] for b in blocks]
    out = bytearray()
    for series in (c0, c1):
        prev = 0
        for v in series:
            out += struct.pack("<H", (v - prev) & 0xFFFF)
            prev = v
    for b in blocks:
        out += b[12:16]
    for b in blocks:
        out += b[0:8]
    assert len(out) == 16 * n
    return bytes(out)


def dxt5_flat_block(rgb565, alpha=255):
    """A block where every pixel is the colour c0=rgb565 (c1 one below, all indices 0)."""
    c1 = rgb565 - 1 if rgb565 else 0
    return bytes([alpha, alpha]) + b"\0" * 6 + struct.pack("<HHI", rgb565, c1, 0)


def build_hd(w, h, fmt, block, stamp=b"$Version: Synthetic 1.0; FE", tail=None, decoy=False, flags=b"\x01\x02\x00\x08"):
    tail = (TAIL_HD_RGBA if fmt in (3, 5) else TAIL_HD_DXT) if tail is None else tail
    return (head(stamp, decoy) + struct.pack("<II", w, h) + flags + struct.pack("<I", fmt) + b"STAR"
            + struct.pack("<I", len(block)) + block + tail)


def build_se2(w, h, bgra, fmt=36, mips_field=1, stamp=b"$Version: Synthetic 2.0", level=6):
    z = zlib.compress(bgra, level)
    return (head(stamp) + struct.pack("<iiIIII", 0, -1, w, h, fmt, mips_field) + b"STAR" + struct.pack("<I", len(z))
            + z + TAIL_SE2)


def pattern(w, h, seed=0):
    """Distinct straight-alpha RGBA per pixel, with a fully transparent row 0."""
    out = bytearray()
    for y in range(h):
        for x in range(w):
            out += bytes(((x * 37 + y * 11 + seed) & 255, (x * 5 + y * 61 + seed) & 255,
                          (x * 23 + y * 7) & 255, 0 if y == 0 else (255 if (x + y) % 3 else 128)))
    return bytes(out)


def box(rgba, w, h):
    """2x2 box, premultiplied weights, fully transparent -> plain mean of the colours (same rule as the module)."""
    nw, nh = max(1, w // 2), max(1, h // 2)
    out = bytearray()
    for y in range(nh):
        for x in range(nw):
            quad = [rgba[((2 * y + j) * w + 2 * x + i) * 4:((2 * y + j) * w + 2 * x + i) * 4 + 4]
                    for j in (0, 1) for i in (0, 1)]
            asum = sum(q[3] for q in quad)
            if asum:
                rgb = [(sum(q[k] * q[3] for q in quad) + asum // 2) // asum for k in range(3)]
            else:
                rgb = [(sum(q[k] for q in quad) + 2) // 4 for k in range(3)]
            out += bytes(rgb) + bytes([(asum + 2) // 4])
    return bytes(out), nw, nh


def rgba_chain(rgba, w, h):
    out = [rgba]
    while (w, h) != (1, 1):
        rgba, w, h = box(rgba, w, h)
        out.append(rgba)
    return out


# --------------------------------------------------------------------------- atlas (several blocks) + flat DXT surfaces
# Flat-colour 4x4 blocks decode exactly, so a test knows every pixel of every mip level without any decoder.

PALETTE_565 = [0xF800, 0x07E0, 0x001F, 0xFFFF, 0x0000, 0xFFE0, 0x07FF, 0xF81F]


def expand565(v):
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def dxt3_flat_block(rgb565):
    return b"\xff" * 8 + struct.pack("<HHI", rgb565, rgb565 - 1 if rgb565 else 0, 0)


def flat_dxt(w, h, fmt=10, seed=0):
    """(stored block, [RGBA bytes of each mip level]) of a DXT texture whose every 4x4 block is one palette colour."""
    blocks, pixels = [], []
    for lvl, (lw, lh) in enumerate(mips(w, h)):
        nbx, nby = max(1, (lw + 3) // 4), max(1, (lh + 3) // 4)
        cols = [PALETTE_565[(seed + lvl * 5 + i * 3 + (i // nbx)) % len(PALETTE_565)] for i in range(nbx * nby)]
        for c in cols:   # c1 = c0 (decodes the same as c1 = c0 - 1) so a block REWRITTEN by an encoder shows in the bytes
            blocks.append(dxt5_flat_block(c) if fmt == 10 else dxt3_flat_block(c))
            blocks[-1] = blocks[-1][:10] + struct.pack("<H", c) + blocks[-1][12:]
        px = bytearray()
        for y in range(lh):
            for x in range(lw):
                px += bytes(expand565(cols[(y // 4) * nbx + x // 4])) + b"\xff"
        pixels.append(bytes(px))
    return hd_dxt_block(blocks), pixels


def natural_blocks(stored_block, count):
    """The natural 16-byte DXT blocks of a stored block, by the module-independent inverse of hd_dxt_block."""
    n = count
    c0 = c1 = 0
    out = []
    for i in range(n):
        c0 = (c0 + struct.unpack_from("<H", stored_block, 2 * i)[0]) & 0xFFFF
        c1 = (c1 + struct.unpack_from("<H", stored_block, 2 * n + 2 * i)[0]) & 0xFFFF
        out.append(stored_block[8 * n + 8 * i:8 * n + 8 * i + 8] + struct.pack("<HH", c0, c1)
                   + stored_block[4 * n + 4 * i:4 * n + 4 * i + 4])
    return out


def build_atlas(entries, stamp=b"$Version: Synthetic HD; atlas", flags=b"\x01\x07\x00\x00"):
    """entries: [(w, h, fmt, stored block)]. Each block is followed by the 81,81 'STAR 0' stub of the real files."""
    out = head(stamp)
    where = []
    for w, h, fmt, block in entries:
        out += struct.pack("<II", w, h) + flags + struct.pack("<I", fmt) + b"STAR" + struct.pack("<I", len(block))
        where.append((len(out) - 8, len(out), len(block)))      # offset of "STAR", first pixel byte, size
        out += block
        out += struct.pack("<IIII", 81, 81, 0, 0) + b"STAR" + struct.pack("<I", 0) + b"\x07\0\0\0"
    return out + TAIL_HD_DXT, where


def model_region(level_pixels, dims, rect, new):
    """What every mip level should hold after writing `new` (RGBA of rect=(x, y, w, h)) into level 0.

    A level-L pixel is recomputed (2x2 premultiplied box of the model's level L-1) exactly when its 2^L-wide
    footprint in level-0 coordinates touches the rectangle; every other pixel keeps its value."""
    x, y, w, h = rect
    out = [bytearray(p) for p in level_pixels]
    for j in range(h):
        out[0][((y + j) * dims[0][0] + x) * 4:((y + j) * dims[0][0] + x + w) * 4] = new[j * w * 4:(j + 1) * w * 4]
    for lvl in range(1, len(dims)):
        lw, lh = dims[lvl]
        pw = dims[lvl - 1][0]
        f = 1 << lvl
        for py in range(lh):
            for px in range(lw):
                if px * f < x + w and (px + 1) * f > x and py * f < y + h and (py + 1) * f > y:
                    quad = bytes(b for yy in (2 * py, 2 * py + 1) for xx in (2 * px, 2 * px + 1)
                                 for b in out[lvl - 1][(yy * pw + xx) * 4:(yy * pw + xx) * 4 + 4])
                    px4, _, _ = box(quad, 2, 2)
                    out[lvl][(py * lw + px) * 4:(py * lw + px) * 4 + 4] = px4
    return [bytes(p) for p in out]
