"""MonoGame .xnb Texture2D container: read the pixel block, write it back in place.

Songs for a Hero - Definitive Edition (Steam 389170) ships tiny XNB files (761-2661 B)
whose whole payload is one uncompressed Color (RGBA) texture. The layout, derived by
walking the header (MonoGame's ContentReader order), is:

  off  size  field
  0    3     magic b"XNB"
  3    1     platform byte: 'w' = Windows (the only one this build uses)
  4    1     format version: 5
  5    1     flags: bit 0x80 = compressed content (refused); 0 = plain
  6    4     uint32 total file size, equal to len(data)
  10   ..    7-bit encoded type reader count; per reader: 7-bit encoded byte length,
             that many UTF-8 bytes of reader name, then int32 reader version
  ..   ..    7-bit encoded index of the type reader of the root object
  ..   0-8   the Texture2D block is located by validation (not a fixed offset):
             some MonoGame builds write a byte or two more before the fields;
             the first offset where surfaceFormat == 0 (Color), mipCount == 1
             and the level data ends exactly at EOF is accepted
  ..   16    Texture2D: int32 surfaceFormat, int32 width, int32 height, int32 mipCount
  ..   4+    per mip level: int32 dataSize, then dataSize bytes of pixels

The shipped textures name "Microsoft.Xna.Framework.Content.Texture2DReader" (47
bytes, no leading slash; 85 = 10 + 1 + 1 + 47 + 4 + 1 + 1 + 16 + 4), surfaceFormat 0 (Color), mipCount 1, so the header ends at 85 and
len(data) == 85 + width*height*4 for every one of the 413 files checked. The parser
derives that offset instead of assuming it (a different reader-name length still
parses) and records whether the file has the validated layout (`standard_layout`).

Pixels are R,G,B,A in memory, straight (non-premultiplied) alpha, rows top-down as
displayed; a fully transparent pixel is (0,0,0,0). Not every .xnb is a texture:
Objects/Videos/*.xnb name a VideoReader and must be refused, as must compressed files
and surface formats other than 0 (DXT etc. do not occur in this game).

Rewriting is size-preserving: the header is carried through untouched, only the pixel
bytes change, so a profile can patch offsets inside the file and undo exactly.

Usage in a profile (one line):

    out = xnb.with_pixels(original, new_block)   # len(out) == len(original)
"""
import struct

MAGIC = b"XNB"
SUPPORTED_VERSION = 5
PLATFORM_WINDOWS = b"w"
FLAG_COMPRESSED = 0x80
SURFACE_COLOR = 0
TEXTURE_READER = "Microsoft.Xna.Framework.Content.Texture2DReader"   # 47 bytes
EXPECTED_DATA_OFFSET = 85   # header size of the build validated on this game
MIN_HEADER = 10

# MonoGame/XNA SurfaceFormat ids, for messages only (only 0 is patchable).
SURFACE_FORMAT_NAMES = {
    0: "Color", 1: "Bgr565", 2: "Bgra5551", 3: "Bgra4444", 4: "Dxt1", 5: "Dxt3",
    6: "Dxt5", 7: "NormalizedByte2", 8: "NormalizedByte4", 9: "Rgba1010102",
    10: "Rg32", 11: "Rgba64", 12: "Alpha8", 13: "Single", 14: "Vector2",
    15: "Vector4", 16: "HalfSingle", 17: "HalfVector2", 18: "HalfVector4",
    19: "HdrBlendable",
}
PLATFORM_NAMES = {
    b"w": "Windows", b"x": "Xbox 360", b"m": "Windows Phone 7",
    b"i": "iOS", b"a": "Android",
}


class XnbError(ValueError):
    """The file is not a texture this plugin can read or write safely."""


class Texture(object):
    """Header facts of one Texture2D XNB; `data_offset`/`pixel_size` bound the pixels."""

    def __repr__(self):
        return "<Texture %dx%d rgba at %d, %d B, format %d>" % (
            self.width, self.height, self.data_offset, self.pixel_size, self.surface_format)


def _varint(data, pos, what):
    """(value, new pos) of a .NET 7-bit encoded int at pos."""
    value = shift = 0
    for _ in range(5):
        if pos >= len(data):
            raise XnbError("truncated %s: 7-bit integer at %d runs past the %d-byte file"
                           % (what, pos, len(data)))
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, pos
    raise XnbError("malformed %s: 7-bit integer at %d is longer than 5 bytes"
                   % (what, pos - 5))


def _int32(data, pos, what):
    if pos + 4 > len(data):
        raise XnbError("truncated %s: int32 at %d needs 4 bytes, only %d left"
                       % (what, pos, len(data) - pos))
    return struct.unpack_from("<i", data, pos)[0], pos + 4


def parse(data):
    """Parse the header of an uncompressed XNB Texture2D; raises XnbError if unsupported."""
    data = bytes(data)
    if len(data) < MIN_HEADER:
        raise XnbError("too short for an XNB header: %d bytes, need at least %d"
                       % (len(data), MIN_HEADER))
    if data[:3] != MAGIC:
        raise XnbError("bad magic %r: expected %r at offset 0" % (data[:3], MAGIC))
    platform = data[3:4]
    if platform != PLATFORM_WINDOWS:
        raise XnbError("unsupported platform byte 0x%02X (%r): expected 0x77 ('w', Windows)"
                       % (platform[0], PLATFORM_NAMES.get(platform, "unknown")))
    version = data[4]
    if version != SUPPORTED_VERSION:
        raise XnbError("unsupported XNB format version %d: only %d is known"
                       % (version, SUPPORTED_VERSION))
    flags = data[5]
    if flags & FLAG_COMPRESSED:
        raise XnbError("compressed XNB (flags 0x%02X has 0x80): only uncompressed "
                       "content can be patched" % flags)
    declared = struct.unpack_from("<I", data, 6)[0]      # uint32, unlike the int32 fields
    pos = 10
    if declared != len(data):
        raise XnbError("declared file size %d does not match the actual %d bytes "
                       "(truncated or extended?)" % (declared, len(data)))

    count, pos = _varint(data, pos, "type reader count")
    if count < 1:
        raise XnbError("no type readers: root object cannot be typed")
    readers = []
    for i in range(count):
        size, pos = _varint(data, pos, "type reader %d name length" % i)
        if pos + size > len(data):
            raise XnbError("truncated type reader %d: name of %d bytes at %d runs past "
                           "the %d-byte file" % (i, size, pos, len(data)))
        readers.append(data[pos:pos + size].decode("utf-8", "replace"))
        pos += size
        _, pos = _int32(data, pos, "type reader %d version" % i)

    root, pos = _varint(data, pos, "root object reader index")
    if root >= len(readers):
        raise XnbError("root reader index %d out of range: only %d reader(s) declared"
                       % (root, len(readers)))
    reader_name = readers[root]
    if not reader_name.endswith("Texture2DReader"):
        raise XnbError("root object uses reader %r, not a Texture2DReader" % reader_name)

    # The Texture2D block follows the root index. Strictly it is read at `pos`
    # (the canonical MonoGame layout); the real build has one stray byte there,
    # so the parser takes the first of the next few offsets that validates
    # end-to-end against the file length (surface must be Color, mips 1, pixel
    # block ending exactly at EOF).
    def _read_block(start):
        """The validated Texture2D fields at `start`, or None if not ours."""
        surface, p1 = _int32(data, start, "surface format")
        if surface != SURFACE_COLOR:
            return None
        width, p2 = _int32(data, p1, "width")
        height, p3 = _int32(data, p2, "height")
        mips, p4 = _int32(data, p3, "mip count")
        data_size, p5 = _int32(data, p4, "level data size")
        if width <= 0 or height <= 0:
            raise XnbError("invalid texture size %dx%d" % (width, height))
        if mips != 1:
            raise XnbError("unsupported mip count %d: only a single level can be patched" % mips)
        if data_size != width * height * 4:
            raise XnbError("level data size %d != width*height*4 = %d for a %dx%d RGBA texture"
                           % (data_size, width * height * 4, width, height))
        if p5 + data_size != len(data):
            return None
        return (surface, width, height, mips, p5, data_size)

    found = None
    first_error = None
    for start in range(pos, pos + 9):
        try:
            found = _read_block(start)
        except XnbError as exc:
            if first_error is None:
                first_error = exc
            found = None
        if found is not None:
            break
    if found is None:
        raise first_error if first_error is not None else XnbError(
            "no valid Texture2D block found after offset %d (surface format must "
            "be 0 Color, mips 1, and the pixel block must end at the end of the "
            "file)" % pos)
    surface, width, height, mips, data_offset, data_size = found

    tex = Texture()
    tex.platform = platform
    tex.version = version
    tex.flags = flags
    tex.total_size = declared
    tex.file_size = len(data)
    tex.readers = readers
    tex.reader_name = reader_name
    tex.surface_format = surface
    tex.width = width
    tex.height = height
    tex.mip_count = mips
    tex.data_offset = data_offset
    tex.pixel_size = data_size
    tex.standard_layout = (reader_name == TEXTURE_READER
                           and data_offset == EXPECTED_DATA_OFFSET)
    return tex


def pixels(data):
    """The RGBA pixel block (width*height*4 bytes) of a texture XNB."""
    tex = parse(data)
    data = bytes(data)
    return data[tex.data_offset:tex.data_offset + tex.pixel_size]


def with_pixels(data, new_block):
    """A copy of the file with only the pixel block replaced; same length, same header."""
    tex = parse(data)
    data = bytes(data)
    block = bytes(new_block)
    if len(block) != tex.pixel_size:
        raise XnbError("new pixel block is %d bytes, expected %d (%dx%d RGBA)"
                       % (len(block), tex.pixel_size, tex.width, tex.height))
    end = tex.data_offset + tex.pixel_size
    out = data[:tex.data_offset] + block + data[end:]
    if len(out) != len(data):
        raise XnbError("replacement changed the file size: %d -> %d"
                       % (len(data), len(out)))
    return out
