"""The XNB Texture2D container (Songs for a Hero): synthetic fixtures are the spec."""
import os
import struct
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from button_glyphs import xnb  # noqa: E402

READER = xnb.TEXTURE_READER
VIDEO_READER = "Microsoft.Xna.Framework.Content.VideoReader"
SAMPLES = os.environ.get("BG_SFH_SAMPLES")


def varint(n):
    """A .NET 7-bit encoded int (MonoGame's string/count encoding)."""
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def pattern(w, h, seed=0):
    """A distinct RGBA pixel per position, with a transparent row and column."""
    out = bytearray()
    for y in range(h):
        for x in range(w):
            out += bytes(((x * 37 + y * 11 + seed) & 255, (x * 5 + y * 61) & 255,
                          (x * 23 + y * 7) & 255, 0 if x == 0 or y == 0 else 255))
    return bytes(out)


def build(w=3, h=2, block=None, reader=READER, surface=0, mips=1, flags=0,
          platform=b"w", version=5, filler=0, root=0, declared=None, data_size=None):
    """One synthetic Texture2D XNB, header written like the game's real files:
    root object index, then an optional stray filler byte, then the Texture2D
    fields, then the pixel block."""
    block = pattern(w, h) if block is None else block
    name = reader.encode("utf-8")
    body = varint(1) + varint(len(name)) + name + struct.pack("<i", 0)   # readers
    body += varint(root)                                                  # root index
    body += bytes(filler)                                                 # stray bytes (real build: 1)
    body += struct.pack("<iiii", surface, w, h, mips)                  # Texture2D fields
    body += struct.pack("<i", len(block) if data_size is None else data_size) + block
    total = 10 + len(body) if declared is None else declared
    return b"XNB" + platform + bytes((version, flags)) + struct.pack("<I", total) + body


def redeclare(data, declared):
    """A file whose size field claims `declared` (to build corrupted inputs)."""
    return data[:6] + struct.pack("<I", declared) + data[10:]


class ParseTest(unittest.TestCase):
    def test_small_texture_fields(self):
        raw = build(3, 2)
        tex = xnb.parse(raw)
        self.assertEqual(tex.platform, b"w")
        self.assertEqual(tex.version, 5)
        self.assertEqual(tex.flags, 0)
        self.assertEqual(tex.total_size, len(raw))
        self.assertEqual(tex.file_size, len(raw))
        self.assertEqual(tex.readers, [READER])
        self.assertEqual(tex.reader_name, READER)
        self.assertEqual(tex.surface_format, 0)
        self.assertEqual((tex.width, tex.height, tex.mip_count), (3, 2, 1))
        self.assertEqual(tex.data_offset, 84)   # 13x13-style header minus padding
        self.assertEqual(tex.pixel_size, 3 * 2 * 4)
        self.assertEqual(tex.data_offset + tex.pixel_size, len(raw))

    def test_13x13_texture_and_the_documented_header_offsets(self):
        raw = build(13, 13)
        tex = xnb.parse(raw)
        self.assertEqual((tex.width, tex.height), (13, 13))
        self.assertEqual(tex.pixel_size, 13 * 13 * 4)
        self.assertEqual(tex.data_offset, 84)
        self.assertEqual(raw[3:4], b"w")
        self.assertEqual((raw[4], raw[5]), (5, 0))
        self.assertEqual(struct.unpack_from("<I", raw, 6)[0], len(raw))
        self.assertEqual(raw[10], 1)                      # one reader
        self.assertEqual(raw[11], len(READER))            # its name length
        self.assertEqual(raw[12:12 + len(READER)].decode(), READER)
        self.assertEqual(struct.unpack_from("<i", raw, 12 + len(READER))[0], 0)
        self.assertEqual(raw[0x40:0x44], struct.pack("<i", 0))        # surfaceFormat
        self.assertEqual(raw[0x44:0x48], struct.pack("<i", 13))       # width
        self.assertEqual(raw[0x48:0x4C], struct.pack("<i", 13))       # height
        self.assertEqual(raw[0x4C:0x50], struct.pack("<i", 1))        # mipCount
        self.assertEqual(raw[0x50:0x54], struct.pack("<i", 13 * 13 * 4))
        self.assertEqual(raw[0x54:], pattern(13, 13))

    def test_offset_is_derived_from_the_reader_name(self):
        short = build(3, 2, reader="Texture2DReader")
        tex = xnb.parse(short)
        self.assertEqual(tex.data_offset, 52)             # not the validated 85
        self.assertFalse(tex.standard_layout)
        self.assertEqual(xnb.pixels(short), pattern(3, 2))

    def test_pixels_are_the_block(self):
        raw = build(13, 13)
        self.assertEqual(xnb.pixels(raw), pattern(13, 13))
        self.assertEqual(len(xnb.pixels(raw)), 13 * 13 * 4)


class WriteTest(unittest.TestCase):
    def test_unchanged_block_round_trip_is_byte_identical(self):
        raw = build(13, 13)
        self.assertEqual(xnb.with_pixels(raw, xnb.pixels(raw)), raw)

    def test_changed_block_keeps_length_and_header(self):
        raw = build(13, 13, filler=1)                     # the real build's layout: offset 85
        block = bytearray(xnb.pixels(raw))
        block[3] = 7                                      # alpha of pixel (0,0)
        block[-4:] = bytes((1, 2, 3, 4))
        out = xnb.with_pixels(raw, block)
        self.assertEqual(len(out), len(raw))
        off = xnb.parse(raw).data_offset
        self.assertEqual(out[:off], raw[:off])            # header untouched
        self.assertEqual(out[off:], bytes(block))
        self.assertEqual(xnb.pixels(out), bytes(block))
        self.assertEqual(xnb.parse(out).standard_layout, True)

    def test_block_size_must_match(self):
        raw = build(13, 13)
        for bad in (pattern(13, 13)[:-4], pattern(13, 13) + b"\0\0\0\0"):
            with self.assertRaises(xnb.XnbError) as ctx:
                xnb.with_pixels(raw, bad)
            self.assertIn("expected %d (13x13 RGBA)" % (13 * 13 * 4), str(ctx.exception))

    def test_bytearray_input_returns_bytes(self):
        raw = bytearray(build(3, 2))
        self.assertEqual(xnb.with_pixels(raw, xnb.pixels(raw)), bytes(raw))
        self.assertIsInstance(xnb.pixels(raw), bytes)


class RefusalTest(unittest.TestCase):
    def check(self, data, *fragments):
        with self.assertRaises(xnb.XnbError) as ctx:
            xnb.parse(data)
        message = str(ctx.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message

    def test_too_short(self):
        self.check(b"XNB", "too short", "need at least 10")

    def test_bad_magic(self):
        bad = b"PNG" + build(3, 2)[3:]
        self.check(bad, "bad magic", "expected b'XNB'")

    def test_bad_version(self):
        self.check(build(3, 2, version=4), "format version 4", "only 5")

    def test_compressed_flag_is_refused(self):
        self.check(build(3, 2, flags=0x80), "compressed XNB", "0x80")

    def test_other_platform_is_refused(self):
        self.check(build(3, 2, platform=b"x"), "platform byte 0x78", "Xbox 360", "'w'")

    def test_declared_size_must_match(self):
        raw = build(3, 2)
        self.check(redeclare(raw, len(raw) + 16), "declared file size %d" % (len(raw) + 16))

    def test_truncated_header(self):
        raw = build(3, 2)[:30]
        self.check(redeclare(raw, len(raw)), "truncated type reader 0", "runs past")

    def test_video_reader_is_refused_by_name(self):
        self.check(build(3, 2, reader=VIDEO_READER), "VideoReader", "not a Texture2DReader")

    def test_other_surface_formats_are_refused(self):
        for code in (4, 6, 12):
            raw = build(3, 2, surface=code)
            with self.assertRaises(xnb.XnbError) as ctx:
                xnb.parse(raw)
            self.assertIn("surface format must be 0 Color", str(ctx.exception))

    def test_mips_are_refused(self):
        self.check(build(3, 2, mips=2), "mip count 2", "single level")

    def test_stray_byte_before_the_texture_block_is_tolerated(self):
        # The real build has one stray byte between the root index and the
        # Texture2D fields; the parser scans instead of assuming one offset.
        raw = build(3, 2, filler=1)
        tex = xnb.parse(raw)
        self.assertEqual((tex.width, tex.height), (3, 2))
        self.assertEqual(xnb.pixels(raw), pattern(3, 2))

    def test_root_index_out_of_range(self):
        self.check(build(3, 2, root=1), "root reader index 1", "only 1 reader")

    def test_data_size_must_be_w_h_4(self):
        self.check(build(3, 2, data_size=3 * 2 * 4 - 4), "level data size 20", "24")

    def test_trailing_bytes_after_the_pixels_are_refused(self):
        raw = build(3, 2) + b"\xDE\xAD\xBE\xEF"
        self.check(redeclare(raw, len(raw)), "surface format must be 0 Color")
        short = build(3, 2)[:-4]                          # pixel block cut short, size field consistent
        self.check(redeclare(short, len(short)),
                   "no valid Texture2D block", "must end at the end of the file")


@unittest.skipUnless(SAMPLES, "set BG_SFH_SAMPLES to a folder with real .xnb files")
class RealSamplesTest(unittest.TestCase):
    """Read-only proof against the game's own files; nothing is written or stored here."""

    def test_every_texture_parses_and_round_trips(self):
        textures = refused = 0
        for folder, _, names in os.walk(SAMPLES):
            for name in names:
                if not name.lower().endswith(".xnb"):
                    continue
                with open(os.path.join(folder, name), "rb") as f:
                    data = f.read()
                try:
                    tex = xnb.parse(data)
                except xnb.XnbError as exc:
                    message = str(exc)
                    self.assertTrue(
                        "VideoReader" in message or "surface format" in message
                        or "reader" in message, (name, message))
                    refused += 1
                    continue
                self.assertEqual(tex.total_size, len(data), name)
                self.assertEqual(tex.data_offset + tex.pixel_size, len(data), name)
                self.assertTrue(tex.standard_layout, name)
                self.assertEqual(tex.data_offset, 85, name)
                block = xnb.pixels(data)
                self.assertEqual(len(block), tex.width * tex.height * 4, name)
                self.assertEqual(xnb.with_pixels(data, block), data, name)
                changed = bytes([block[0] ^ 0xFF]) + block[1:]
                out = xnb.with_pixels(data, changed)
                self.assertEqual(len(out), len(data), name)
                self.assertEqual(out[:85], data[:85], name)
                self.assertEqual(out[85:], changed, name)
                textures += 1
        self.assertGreater(textures, 0, "no texture .xnb found under %s" % SAMPLES)


if __name__ == "__main__":
    unittest.main()
