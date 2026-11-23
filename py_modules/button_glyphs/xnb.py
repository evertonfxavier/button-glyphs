"""XNA/FNA .xnb content files: LZX decompression and Texture2D access (stdlib only).

An .xnb is   'XNB' platform version flags size32 [decompressed_size32] payload.
flags 0x80 = payload is LZX compressed (window 64 KB, chunks of <= 32 KB output).
We only ever *write* uncompressed files (flags without 0x80): every XNA/FNA runtime
reads those, and it spares us an LZX encoder.

The payload is  type-reader table, shared-resource count, then the primary object.
For a Texture2D the object is  surface format, width, height, mip count, then per mip
a u32 byte size followed by the pixels.
"""
import struct

COMPRESSED = 0x80
HEADER = 10

# Texture2D SurfaceFormat values (XNA 4 / FNA)
SURFACE = {0: "RGBA", 4: "DXT1", 5: "DXT3", 6: "DXT5"}


class XnbError(Exception):
    pass


# ------------------------------------------------------------------------ LZX


def _tables():
    extra = [0] * 52
    j = 0
    for i in range(0, 51, 2):
        extra[i] = extra[i + 1] = j
        if i != 0 and j < 17:
            j += 1
    base, j = [0] * 52, 0
    for i in range(51):
        base[i] = j
        j += 1 << extra[i]
    return extra, base


EXTRA_BITS, POSITION_BASE = _tables()
NUM_CHARS = 256
NUM_SECONDARY = 249
PRETREE_SYMS = 20
ALIGNED_SYMS = 8


class _Huffman:
    """Canonical Huffman decoder (codes of at most 16 bits)."""

    def __init__(self, lengths):
        self.lengths = lengths
        self.min = 17
        self.max = 0
        count = [0] * 17
        for n in lengths:
            if n:
                count[n] += 1
        for n in range(1, 17):
            if count[n]:
                self.min = min(self.min, n)
                self.max = n
        self.symbols = [s for n in range(1, 17) for s, ln in enumerate(lengths) if ln == n]
        self.first, self.count, self.offset = [0] * 17, count, [0] * 17
        code = off = 0
        for n in range(1, 17):
            self.first[n] = code
            self.offset[n] = off
            code = (code + count[n]) << 1
            off += count[n]


class _Bits:
    """MSB-first bit reader over little-endian 16-bit words."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.buf = 0
        self.left = 0

    def _fill(self, n):
        while self.left < n:
            d = self.data
            w = d[self.pos] | (d[self.pos + 1] << 8) if self.pos + 1 < len(d) else 0
            self.pos += 2
            self.buf = (self.buf << 16) | w
            self.left += 16

    def read(self, n):
        if n == 0:
            return 0
        self._fill(n)
        self.left -= n
        v = self.buf >> self.left
        self.buf &= (1 << self.left) - 1
        return v

    def decode(self, tree):
        self._fill(16)
        peek = (self.buf >> (self.left - 16)) & 0xFFFF
        for n in range(tree.min, tree.max + 1):
            code = peek >> (16 - n)
            k = code - tree.first[n]
            if 0 <= k < tree.count[n]:
                self.left -= n
                self.buf &= (1 << self.left) - 1
                return tree.symbols[tree.offset[n] + k]
        raise XnbError("invalid LZX Huffman code")

    def align16(self):
        self.read(self.left & 15)
        # give unread whole words back to the input
        self.pos -= self.left // 8
        self.buf = 0
        self.left = 0

    def bytes(self, n):
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out


class LzxDecoder:
    """State persists across chunks (window, repeated offsets, previous tree lengths)."""

    def __init__(self, window_bits=16):
        slots = {15: 30, 16: 32, 17: 34, 18: 36, 19: 38, 20: 42, 21: 50}[window_bits]
        self.main_size = NUM_CHARS + slots * 8
        self.out = bytearray()
        self.R = [1, 1, 1]
        self.header = False
        self.intel_size = 0
        self.block_type = 0
        self.block_remaining = 0
        self.main_len = [0] * self.main_size
        self.length_len = [0] * NUM_SECONDARY
        self.main = self.length = self.aligned = None
        self.frames = 0

    def _read_lengths(self, bits, lens, first, last):
        pre = _Huffman([bits.read(4) for _ in range(PRETREE_SYMS)])
        x = first
        while x < last:
            z = bits.decode(pre)
            if z == 17:
                y = bits.read(4) + 4
                lens[x:x + y] = [0] * y
                x += y
            elif z == 18:
                y = bits.read(5) + 20
                lens[x:x + y] = [0] * y
                x += y
            elif z == 19:
                y = bits.read(1) + 4
                z = lens[x] - bits.decode(pre)
                z += 17 if z < 0 else 0
                lens[x:x + y] = [z] * y
                x += y
            else:
                z = lens[x] - z
                lens[x] = z + (17 if z < 0 else 0)
                x += 1

    def _block_header(self, bits):
        if self.block_type == 3 and self.block_size & 1:
            bits.pos += 1
        self.block_type = bits.read(3)
        hi = bits.read(16)
        lo = bits.read(8)
        self.block_size = self.block_remaining = (hi << 8) | lo
        t = self.block_type
        if t == 2:
            self.aligned = _Huffman([bits.read(3) for _ in range(ALIGNED_SYMS)])
        if t in (1, 2):
            self._read_lengths(bits, self.main_len, 0, NUM_CHARS)
            self._read_lengths(bits, self.main_len, NUM_CHARS, self.main_size)
            self._read_lengths(bits, self.length_len, 0, NUM_SECONDARY)
            self.main = _Huffman(self.main_len)
            self.length = _Huffman(self.length_len)
        elif t == 3:
            bits.align16()
            self.R = [struct.unpack_from("<I", bits.bytes(4))[0] for _ in range(3)]
        else:
            raise XnbError("bad LZX block type %d" % t)

    def decompress_chunk(self, data, frame_size):
        """Decode one chunk of compressed bytes into `frame_size` output bytes."""
        bits = _Bits(data)
        if not self.header:
            if bits.read(1):
                self.intel_size = (bits.read(16) << 16) | bits.read(16)
            self.header = True
        out = self.out
        start = len(out)
        todo = frame_size
        R = self.R
        while todo > 0:
            if self.block_remaining == 0:
                self._block_header(bits)
                R = self.R
            run = min(self.block_remaining, todo)
            todo -= run
            self.block_remaining -= run
            if self.block_type == 3:
                out += bits.bytes(run)
                continue
            aligned = self.block_type == 2
            while run > 0:
                sym = bits.decode(self.main)
                if sym < NUM_CHARS:
                    out.append(sym)
                    run -= 1
                    continue
                sym -= NUM_CHARS
                length = sym & 7
                if length == 7:
                    length += bits.decode(self.length)
                length += 2
                slot = sym >> 3
                if slot == 0:
                    offset = R[0]
                elif slot < 3:
                    offset = R[slot]
                    R[slot] = R[0]
                    R[0] = offset
                else:
                    eb = EXTRA_BITS[slot]
                    if aligned and eb >= 3:
                        verbatim = bits.read(eb - 3) if eb > 3 else 0
                        offset = POSITION_BASE[slot] - 2 + (verbatim << 3) + bits.decode(self.aligned)
                    else:
                        offset = POSITION_BASE[slot] - 2 + bits.read(eb)
                    R[2], R[1], R[0] = R[1], R[0], offset
                run -= length
                pos = len(out)
                if offset > pos:
                    raise XnbError("LZX match before start of data")
                if offset >= length:
                    out += out[pos - offset:pos - offset + length]
                else:
                    for _ in range(length):
                        out.append(out[-offset])
            if run < 0:
                self.block_remaining -= -run
                todo += run
        if self.intel_size and len(out) - start > 10:
            self._e8(start)
        return len(out) - start

    def _e8(self, start):
        out, size = self.out, self.intel_size
        end = len(out) - 10
        i = start
        while i < end:
            if out[i] != 0xE8:
                i += 1
                continue
            cur = i  # position in the whole stream (each chunk is one 32 KB frame)
            absv = struct.unpack_from("<i", out, i + 1)[0]
            if -cur <= absv < size:
                rel = absv - cur if absv >= 0 else absv + size
                struct.pack_into("<i", out, i + 1, rel)
            i += 5


def decompress(data, size):
    """LZX-decode the chunk stream `data` (XNB payload) into `size` bytes."""
    dec = LzxDecoder(16)
    p = 0
    while len(dec.out) < size and p < len(data):
        if data[p] == 0xFF:
            frame = (data[p + 1] << 8) | data[p + 2]
            block = (data[p + 3] << 8) | data[p + 4]
            p += 5
        else:
            frame = 0x8000
            block = (data[p] << 8) | data[p + 1]
            p += 2
        if block == 0 or frame == 0:
            break
        dec.decompress_chunk(data[p:p + block], frame)
        p += block
    if len(dec.out) < size:
        raise XnbError("LZX stream ended early (%d of %d bytes)" % (len(dec.out), size))
    return bytes(dec.out[:size])


# ------------------------------------------------------------------------ XNB container


def read_xnb(data):
    """Return (header 5 bytes 'XNB'+platform+version, flags, payload) with payload decompressed."""
    if data[:3] != b"XNB":
        raise XnbError("not an XNB file")
    flags = data[5]
    total = struct.unpack_from("<I", data, 6)[0]
    if total != len(data):
        raise XnbError("XNB size field %d != file size %d" % (total, len(data)))
    if flags & COMPRESSED:
        size = struct.unpack_from("<I", data, HEADER)[0]
        payload = decompress(data[HEADER + 4:], size)
    else:
        payload = data[HEADER:]
    return data[:5], flags, payload


def write_xnb(head, flags, payload):
    """Build an uncompressed XNB."""
    flags &= ~COMPRESSED
    size = HEADER + len(payload)
    return head + bytes([flags]) + struct.pack("<I", size) + payload


# ------------------------------------------------------------------------ Texture2D


def _read7(buf, p):
    n = shift = 0
    while True:
        b = buf[p]
        p += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, p
        shift += 7


def texture_layout(payload):
    """Locate the pixels of a single-object Texture2D payload.

    Returns dict(format, width, height, mips, offset, size) for the top mip level.
    """
    p = 0
    readers, p = _read7(payload, p)
    names = []
    for _ in range(readers):
        n, p = _read7(payload, p)
        names.append(payload[p:p + n].decode("ascii", "replace"))
        p += n
        p += 4  # reader version
    shared, p = _read7(payload, p)
    if shared:
        raise XnbError("shared resources are not supported")
    p = _read7(payload, p)[1]  # type-reader index of the object (0 = null, else index+1)
    fmt, w, h, mips = struct.unpack_from("<4I", payload, p)
    p += 16
    size = struct.unpack_from("<I", payload, p)[0]
    if not names or not names[0].startswith("Microsoft.Xna.Framework.Content.Texture2DReader"):
        raise XnbError("not a Texture2D (%s)" % (names[:1],))
    return {"format": SURFACE.get(fmt, fmt), "surface": fmt, "width": w, "height": h,
            "mips": mips, "offset": p + 4, "size": size}
