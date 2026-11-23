"""Pixel helpers: DXT1/3/5 codecs, PNG I/O and area resampling (stdlib only).

Images are (rgba: bytes, width, height) with 8-bit straight (non-premultiplied) alpha.
"""
import struct
import zlib


# --------------------------------------------------------------------------- DXT decode


def _c565(c):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _palette(c0, c1, four_color):
    p0, p1 = _c565(c0), _c565(c1)
    if four_color:
        p2 = tuple((2 * a + b) // 3 for a, b in zip(p0, p1))
        p3 = tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))
        return [p0 + (255,), p1 + (255,), p2 + (255,), p3 + (255,)]
    p2 = tuple((a + b) // 2 for a, b in zip(p0, p1))
    return [p0 + (255,), p1 + (255,), p2 + (255,), (0, 0, 0, 0)]


def decode(data, w, h, fmt):
    """Decode a DXT1/DXT3/DXT5 top-level surface to RGBA."""
    rgba = bytearray(w * h * 4)
    bs = 8 if fmt == "DXT1" else 16
    p = 0
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            blk = data[p:p + bs]
            p += bs
            cblk = blk if fmt == "DXT1" else blk[8:]
            c0, c1, idx = struct.unpack_from("<HHI", cblk, 0)
            pal = _palette(c0, c1, c0 > c1 or fmt != "DXT1")
            alpha = None
            if fmt == "DXT3":
                bits = int.from_bytes(blk[:8], "little")
                alpha = [((bits >> (4 * i)) & 15) * 17 for i in range(16)]
            elif fmt == "DXT5":
                a0, a1 = blk[0], blk[1]
                bits = int.from_bytes(blk[2:8], "little")
                if a0 > a1:
                    al = [a0, a1] + [((6 - i) * a0 + (i + 1) * a1) // 7 for i in range(6)]
                else:
                    al = [a0, a1] + [((4 - i) * a0 + (i + 1) * a1) // 5 for i in range(4)] + [0, 255]
                alpha = [al[(bits >> (3 * i)) & 7] for i in range(16)]
            for i in range(16):
                x, y = bx + (i & 3), by + (i >> 2)
                if x >= w or y >= h:
                    continue
                o = (y * w + x) * 4
                px = pal[(idx >> (2 * i)) & 3]
                rgba[o:o + 4] = bytes(px)
                if alpha is not None:
                    rgba[o + 3] = alpha[i]
    return bytes(rgba)


# --------------------------------------------------------------------------- DXT encode


def _to565(r, g, b):
    return ((r * 31 + 127) // 255) << 11 | ((g * 63 + 127) // 255) << 5 | ((b * 31 + 127) // 255)


def _encode_color(px):
    """4-colour DXT block. Endpoints: extremes along the colour axis of the visible pixels."""
    vis = [p for p in px if p[3] >= 16] or px
    mean = [sum(p[k] for p in vis) / len(vis) for k in range(3)]
    # principal axis by power iteration on the covariance
    cov = [[sum((p[i] - mean[i]) * (p[j] - mean[j]) for p in vis) for j in range(3)] for i in range(3)]
    axis = [1.0, 1.0, 1.0]
    for _ in range(4):
        axis = [sum(cov[i][j] * axis[j] for j in range(3)) for i in range(3)]
        norm = max(abs(v) for v in axis) or 1.0
        axis = [v / norm for v in axis]
    proj = [sum((p[k] - mean[k]) * axis[k] for k in range(3)) for p in vis]
    hi = vis[proj.index(max(proj))]
    lo = vis[proj.index(min(proj))]
    c0, c1 = _to565(*hi[:3]), _to565(*lo[:3])
    if c0 < c1:
        c0, c1 = c1, c0
    if c0 == c1:  # keep 4-colour mode (c0 > c1)
        if c0 == 0:
            c0 = 1
        else:
            c1 = c0 - 1
    pal = _palette(c0, c1, True)
    idx = 0
    for i, p in enumerate(px):
        best = min(range(4), key=lambda k: (p[0] - pal[k][0]) ** 2 + (p[1] - pal[k][1]) ** 2
                   + (p[2] - pal[k][2]) ** 2)
        idx |= best << (2 * i)
    return struct.pack("<HHI", c0, c1, idx)


def _encode_alpha5(px):
    alphas = [p[3] for p in px]
    a0, a1 = max(alphas), min(alphas)
    if a0 == a1:
        return bytes([a0, a1]) + b"\0" * 6
    al = [a0, a1] + [((6 - i) * a0 + (i + 1) * a1) // 7 for i in range(6)]
    bits = 0
    for i, a in enumerate(alphas):
        bits |= min(range(8), key=lambda k: abs(a - al[k])) << (3 * i)
    return bytes([a0, a1]) + bits.to_bytes(6, "little")


def encode_block(px, fmt):
    """px: 16 RGBA tuples (row-major 4x4)."""
    if fmt == "DXT3":
        bits = 0
        for i, p in enumerate(px):
            bits |= ((p[3] * 15 + 127) // 255) << (4 * i)
        return bits.to_bytes(8, "little") + _encode_color(px)
    if fmt == "DXT5":
        return _encode_alpha5(px) + _encode_color(px)
    if fmt == "DXT1":
        return _encode_color(px)
    raise ValueError("unsupported format %s" % fmt)


def block_pixels(rgba, w, bx, by):
    px = []
    for y in range(4):
        o = ((by * 4 + y) * w + bx * 4) * 4
        for x in range(4):
            px.append(tuple(rgba[o + 4 * x:o + 4 * x + 4]))
    return px


# --------------------------------------------------------------------------- PNG


def write_png(path, rgba, w, h):
    raw = b"".join(b"\0" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 9)))
        f.write(chunk(b"IEND", b""))


def read_png(path):
    """8-bit RGB/RGBA, non-interlaced PNG -> (rgba, w, h)."""
    with open(path, "rb") as f:
        d = f.read()
    if d[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG: %s" % path)
    p, idat = 8, b""
    w = h = ct = None
    while p < len(d):
        ln, t = struct.unpack_from(">I4s", d, p)
        body = d[p + 8:p + 8 + ln]
        if t == b"IHDR":
            w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", body)
            if bd != 8 or ct not in (2, 6) or il:
                raise ValueError("unsupported PNG layout in %s" % path)
        elif t == b"IDAT":
            idat += body
        p += 12 + ln
    bpp = 4 if ct == 6 else 3
    raw = zlib.decompress(idat)
    stride = w * bpp
    out = bytearray()
    prev = bytearray(stride)
    p = 0
    for _ in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif ft == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else (b if pb <= pc else c))) & 255
        prev = line
        if bpp == 4:
            out += line
        else:
            for i in range(0, stride, 3):
                out += line[i:i + 3] + b"\xff"
    return bytes(out), w, h


# --------------------------------------------------------------------------- resampling


def _weights(src, dst):
    """For each destination pixel, the (source index, coverage) pairs of an area filter."""
    scale = src / dst
    table = []
    for d in range(dst):
        a, b = d * scale, (d + 1) * scale
        row = []
        i = int(a)
        while i < b and i < src:
            cover = min(b, i + 1) - max(a, i)
            if cover > 0:
                row.append((i, cover / scale))
            i += 1
        table.append(row)
    return table


def resize(rgba, w, h, nw, nh):
    """Area (box) resample in premultiplied space, so transparent edges stay clean."""
    pre = [0.0] * (w * h * 4)
    for i in range(w * h):
        a = rgba[i * 4 + 3] / 255.0
        pre[i * 4] = rgba[i * 4] * a
        pre[i * 4 + 1] = rgba[i * 4 + 1] * a
        pre[i * 4 + 2] = rgba[i * 4 + 2] * a
        pre[i * 4 + 3] = float(rgba[i * 4 + 3])
    wx, wy = _weights(w, nw), _weights(h, nh)
    tmp = [0.0] * (nw * h * 4)
    for y in range(h):
        for x, row in enumerate(wx):
            for k in range(4):
                tmp[(y * nw + x) * 4 + k] = sum(pre[(y * w + i) * 4 + k] * c for i, c in row)
    out = bytearray(nw * nh * 4)
    for y, row in enumerate(wy):
        for x in range(nw):
            acc = [sum(tmp[(i * nw + x) * 4 + k] * c for i, c in row) for k in range(4)]
            a = acc[3]
            o = (y * nw + x) * 4
            if a > 0.5:
                f = 255.0 / a
                out[o:o + 4] = bytes(min(255, int(acc[k] * f + 0.5)) for k in range(3)) + bytes(
                    [min(255, int(a + 0.5))])
    return bytes(out)


def fit_icon(rgba, w, h, box_w, box_h):
    """Scale an icon to fit inside box_w x box_h keeping aspect ratio."""
    s = min(box_w / w, box_h / h)
    nw, nh = max(1, int(round(w * s))), max(1, int(round(h * s)))
    return resize(rgba, w, h, nw, nh), nw, nh
