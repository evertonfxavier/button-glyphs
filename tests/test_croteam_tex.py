"""Croteam .tex and .gro (Serious Sam HD FE/TSE, Serious Sam 2): synthetic fixtures are the spec."""
import hashlib
import os
import random
import struct
import sys
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, HERE)

from button_glyphs import croteam_tex as T  # noqa: E402
from button_glyphs import gro, image  # noqa: E402
import croteam_fixture as F  # noqa: E402

FE = b"$Version: Synthetic HD; FE build 1"
TSE = b"$Version: Synthetic HD; TSE build 2 (other stamp)"


def rgba_tex(w=8, h=8, fmt=5, stamp=FE, seed=0, **kw):
    pic = F.pattern(w, h, seed)
    block = F.hd_rgba_block(F.rgba_chain(pic, w, h))
    return F.build_hd(w, h, fmt, block, stamp=stamp, **kw), pic


def dxt_tex(w=8, h=8, fmt=10, stamp=FE):
    """DXT5/DXT3 texture whose every 4x4 block is flat colour, levels down to 1x1."""
    blocks = []
    for lw, lh in F.mips(w, h):
        for i in range(max(1, (lw + 3) // 4) * max(1, (lh + 3) // 4)):
            blocks.append(F.dxt5_flat_block(0xF800 if i % 2 else 0x07E0))
    return F.build_hd(w, h, fmt, F.hd_dxt_block(blocks), stamp=stamp), blocks


class FilterTest(unittest.TestCase):
    def test_rgba_is_four_delta_planes(self):
        # 2x2 level + 1x1 level = 5 pixels; plane k holds byte k of every pixel, delta coded
        px = [(10, 20, 30, 255), (13, 18, 30, 0), (9, 20, 40, 7), (255, 0, 1, 2), (1, 2, 3, 4)]
        natural = bytes(v for p in px for v in p)
        stored = T.filter_surface(natural, "rgba8")
        self.assertEqual(stored, F.hd_rgba_block([natural[:16], natural[16:]]))
        self.assertEqual(list(stored[:5]), [10, 3, (9 - 13) & 255, (255 - 9) & 255, (1 - 255) & 255])
        self.assertEqual(T.unfilter(stored, "rgba8"), natural)

    def test_dxt_is_four_sections(self):
        blocks = [bytes(range(16)), bytes(range(16, 32)), bytes([200] * 16)]
        natural = b"".join(blocks)
        stored = T.filter_surface(natural, "dxt5")
        n = 3
        c0 = [struct.unpack("<H", b[8:10])[0] for b in blocks]
        self.assertEqual(struct.unpack_from("<H", stored, 0)[0], c0[0])
        self.assertEqual(struct.unpack_from("<H", stored, 2)[0], (c0[1] - c0[0]) & 0xFFFF)
        self.assertEqual(stored[4 * n:4 * n + 4], blocks[0][12:16])    # colour indices, plain
        self.assertEqual(stored[8 * n:8 * n + 8], blocks[0][0:8])      # alpha blocks, plain
        self.assertEqual(stored, F.hd_dxt_block(blocks))
        self.assertEqual(T.unfilter(stored, "dxt5"), natural)

    def test_filter_is_a_bijection_for_any_bytes(self):
        rnd = random.Random(1)
        for name, size in (("rgba8", 4 * 21), ("dxt5", 16 * 7), ("dxt3", 16 * 3)):
            for _ in range(20):
                body = bytes(rnd.randrange(256) for _ in range(size))
                self.assertEqual(T.filter_surface(T.unfilter(body, name), name), body)


class ParseTest(unittest.TestCase):
    def test_untouched_parse_is_identical(self):
        files = [rgba_tex()[0], rgba_tex(fmt=3, stamp=TSE)[0], dxt_tex()[0], dxt_tex(fmt=11)[0]]
        files.append(F.build_se2(4, 4, bytes(range(64))))
        files.append(F.build_hd(4, 4, 6, bytes(2 * 21)))           # 16-bit: parse only
        for data in files:
            self.assertEqual(T.parse(data).tobytes(), data)

    def test_hd_fields(self):
        data, _ = rgba_tex(4, 4, fmt=3)
        t = T.parse(data)
        self.assertEqual((t.kind, t.width, t.height, t.fmt, t.fmt_name), ("hd", 4, 4, 3, "rgba8"))
        self.assertTrue(t.supported)
        t = T.parse(dxt_tex(fmt=11)[0])
        self.assertEqual((t.fmt_name, t.width, t.height), ("dxt3", 8, 8))
        self.assertEqual(T.parse(F.build_hd(4, 4, 6, bytes(2 * 21))).fmt_name, "bits16")
        self.assertFalse(T.parse(F.build_hd(4, 4, 6, bytes(2 * 21))).supported)

    def test_the_trailer_star_and_a_decoy_do_not_confuse_the_locator(self):
        data, pic = rgba_tex(decoy=True)
        self.assertGreaterEqual(data.count(b"STAR"), 3)
        self.assertEqual(T.decode(data)[0], pic)

    def test_se2_fields_and_pixels_are_bgra(self):
        bgra = bytes([1, 2, 3, 4]) * 16
        t = T.parse(F.build_se2(4, 4, bgra))
        self.assertEqual((t.kind, t.fmt, t.fmt_name), ("se2", 36, "bgra8"))
        self.assertEqual(t.pixels(), (bytes([3, 2, 1, 4]) * 16, 4, 4))

    def test_se2_with_mips_or_odd_size_is_parsed_but_not_writable(self):
        t = T.parse(F.build_se2(4, 4, bytes(64), mips_field=4))
        self.assertFalse(t.supported)
        with self.assertRaises(T.TexError):
            t.with_pixels(bytes(64))
        with self.assertRaises(T.TexError):
            t.pixels()

    def test_refusals(self):
        good, _ = rgba_tex()
        for bad in (b"", b"CTSEMETA", b"X" + good[1:], good[:-70], good.replace(b"STAR", b"STAX")):
            with self.assertRaises(T.TexError):
                T.parse(bad)
        # block size not matching w*h*format -> not believed
        bad_size = F.build_hd(8, 8, 5, bytes(100))
        with self.assertRaises(T.TexError):
            T.parse(bad_size)
        # unknown format id
        with self.assertRaises(T.TexError):
            T.parse(F.build_hd(8, 8, 99, bytes(4 * 85)))
        # zlib garbage behind an SE2-looking header
        se2 = bytearray(F.build_se2(4, 4, bytes(64)))
        i = se2.find(b"STAR") + 8
        se2[i + 2:i + 6] = b"\xff\xff\xff\xff"
        with self.assertRaises(T.TexError):
            T.parse(bytes(se2))


class DecodeEncodeTest(unittest.TestCase):
    def test_rgba_level0_decodes_to_the_picture_and_mips_are_stored(self):
        data, pic = rgba_tex(8, 8)
        t = T.parse(data)
        self.assertEqual(t.pixels(), (pic, 8, 8))
        levels = t.mip_pixels()
        self.assertEqual([(w, h) for _, w, h in levels], [(8, 8), (4, 4), (2, 2), (1, 1)])
        self.assertEqual([p for p, _, _ in levels], F.rgba_chain(pic, 8, 8))

    def test_dxt_flat_blocks_decode(self):
        data, _ = dxt_tex(8, 8)
        px, w, h = T.decode(data)
        self.assertEqual((w, h), (8, 8))
        # block 0 is green (0x07E0), block 1 red (0xF800): left 4 columns green, right 4 red
        self.assertEqual(tuple(px[0:4]), (0, 255, 0, 255))
        self.assertEqual(tuple(px[16:20]), (255, 0, 0, 255))

    def test_reencode_rgba_is_exact_and_keeps_everything_else(self):
        for fmt in (3, 5):
            data, _ = rgba_tex(8, 8, fmt=fmt, stamp=TSE)
            new = F.pattern(8, 8, seed=99)
            out = T.reencode(data, new)
            a, b = T.parse(data), T.parse(out)
            self.assertEqual(len(out), len(data))
            self.assertEqual((a.head, a.tail, a.fmt), (b.head, b.tail, b.fmt))   # incl. the version stamp
            self.assertIn(TSE, out)
            self.assertEqual(b.pixels(), (new, 8, 8))
            self.assertEqual([p for p, _, _ in b.mip_pixels()], F.rgba_chain(new, 8, 8))

    def test_reencode_with_the_same_pixels_changes_nothing_when_mips_agree(self):
        data, pic = rgba_tex(8, 8)
        self.assertEqual(T.reencode(data, pic), data)    # fixture mips are our own box filter

    def test_mips_follow_the_new_picture_and_keep_the_colour_under_transparency(self):
        pic = bytearray(F.pattern(4, 4))
        for i in range(0, 8 * 4, 4):                      # rows 0-1 fully transparent, colour (41,157,20)
            pic[i:i + 4] = bytes((41, 157, 20, 0))
        data, _ = rgba_tex(4, 4)
        out = T.parse(T.reencode(data, bytes(pic)))
        lvl1 = out.mip_pixels()[1][0]
        self.assertEqual(tuple(lvl1[0:4]), (41, 157, 20, 0))   # bled colour survives, alpha 0

    def test_reencode_dxt_flat_picture_round_trips(self):
        for fmt in (10, 11):
            data, _ = dxt_tex(8, 8, fmt=fmt)
            pic = bytearray()
            for y in range(8):
                for x in range(8):
                    pic += bytes((255, 0, 0, 255)) if x < 4 else bytes((0, 0, 0, 255))
            out = T.reencode(data, bytes(pic))
            self.assertEqual(len(out), len(data))
            self.assertEqual(T.parse(out).tail, T.parse(data).tail)
            self.assertEqual(T.decode(out)[0], bytes(pic))

    def test_reencode_dxt_keeps_soft_alpha_within_tolerance(self):
        data, _ = dxt_tex(8, 8)
        pic = bytearray()
        for y in range(8):
            for x in range(8):
                pic += bytes((250, 250, 250, min(255, 40 * x + 8 * y)))
        got = T.decode(T.reencode(data, bytes(pic)))[0]
        worst = max(abs(a - b) for a, b in zip(got, pic))
        self.assertLessEqual(worst, 24)

    def test_dxt_mips_smaller_than_a_block_are_padded(self):
        data, _ = dxt_tex(4, 4)               # levels 4x4, 2x2, 1x1 = 3 blocks
        pic = bytes((255, 0, 0, 255)) * 16
        out = T.parse(T.reencode(data, pic))
        self.assertEqual([(w, h) for _, w, h in out.mip_pixels()], [(4, 4), (2, 2), (1, 1)])
        self.assertEqual(out.mip_pixels()[2][0], bytes((255, 0, 0, 255)))

    def test_se2_reencode_is_exact_and_same_with_zlib_level_6(self):
        bgra = bytes(((x * 7) & 255 for x in range(16 * 16 * 4)))
        data = F.build_se2(16, 16, bgra)
        t = T.parse(data)
        self.assertEqual(T.reencode(data, t.pixels()[0]), data)          # byte for byte
        new = F.pattern(16, 16, 5)
        out = T.reencode(data, new)
        a, b = T.parse(data), T.parse(out)
        self.assertEqual((a.head, a.tail), (b.head, b.tail))
        self.assertEqual(b.pixels(), (new, 16, 16))
        n = struct.unpack_from("<I", out, out.find(b"STAR") + 4)[0]
        self.assertEqual(n, len(b.stored))                               # the only changed field

    def test_wrong_pixel_count_is_refused(self):
        data, _ = rgba_tex(8, 8)
        for bad in (b"", bytes(8 * 8 * 4 - 1), bytes(16 * 16 * 4)):
            with self.assertRaises(T.TexError):
                T.reencode(data, bad)
        with self.assertRaises(T.TexError):
            T.reencode(F.build_hd(4, 4, 6, bytes(2 * 21)), bytes(64))    # 16-bit is not written

    def test_half_matches_the_generic_resampler_on_opaque_pixels(self):
        pic = bytearray(F.pattern(8, 8))
        pic[3::4] = b"\xff" * 64
        fast, w, h = T.half(bytes(pic), 8, 8)
        slow = image.resize(bytes(pic), 8, 8, 4, 4)
        self.assertEqual((w, h), (4, 4))
        self.assertLessEqual(max(abs(a - b) for a, b in zip(fast, slow)), 1)


def covering(rect, lvl, lw, lh):
    """Independent model of the blocks (indices) a region write may touch at mip `lvl`: the level pixels whose
    2^lvl footprint touches the level-0 rectangle, widened to whole 4x4 blocks."""
    x, y, w, h = rect
    f = 1 << lvl
    px0, px1 = x // f, min(lw, -(-(x + w) // f))
    py0, py1 = y // f, min(lh, -(-(y + h) // f))
    nbx = max(1, (lw + 3) // 4)
    return {by * nbx + bx for by in range(py0 // 4, (py1 + 3) // 4) for bx in range(px0 // 4, (px1 + 3) // 4)}


EXACT_565 = {F.expand565(v) for v in range(1 << 16)}      # RGB triples a 565 endpoint can hold


class AtlasBlocksTest(unittest.TestCase):
    SIZES = ((16, 16, 10), (32, 32, 10), (64, 64, 11))

    def make(self, plant=False, sizes=None):
        entries, pix = [], []
        for k, (w, h, fmt) in enumerate(sizes or self.SIZES):
            block, px = F.flat_dxt(w, h, fmt, seed=k)
            if plant and k == 1:   # a believable fake block header + "STAR" + exact size INSIDE the pixels
                fake = struct.pack("<IIII", 4, 4, 0x0107, 10) + b"STAR" + struct.pack("<I", 48) + bytes(48)
                block = block[:64] + fake + block[64 + len(fake):]
            entries.append((w, h, fmt, block))
            pix.append(px)
        data, where = F.build_atlas(entries)
        return data, where, pix

    def test_find_blocks_lists_every_block_in_file_order(self):
        data, where, _ = self.make()
        blocks = T.find_blocks(data)
        self.assertEqual([(b.offset, b.start, b.size) for b in blocks], where)
        self.assertEqual([(b.width, b.height, b.fmt, b.fmt_name, b.index) for b in blocks],
                         [(16, 16, 10, "dxt5", 0), (32, 32, 10, "dxt5", 1), (64, 64, 11, "dxt3", 2)])
        self.assertEqual(data.count(b"STAR"), 3 + 3 + 1)      # 3 blocks, 3 stubs and the trailer's own STAR are decoys

    def test_a_fake_block_inside_the_pixels_is_not_found(self):
        data, where, _ = self.make(plant=True)
        self.assertIn(struct.pack("<I", 48) + bytes(48), data)
        self.assertEqual([(b.offset, b.size) for b in T.find_blocks(data)], [(o, n) for o, _, n in where])

    def test_selection_by_index_or_shape_never_guesses(self):
        data, _, _ = self.make()
        self.assertEqual(T.get_block(data, index=2).width, 64)
        self.assertEqual(T.get_block(data, w=32, h=32).index, 1)
        self.assertEqual(T.get_block(data, fmt_name="dxt3").index, 2)
        for kw in ({"index": 3}, {"index": -1}, {"w": 99}, {"fmt_name": "dxt5"}, {}):   # none / two / all match
            with self.assertRaises(T.TexError):
                T.get_block(data, **kw)

    def test_parse_still_means_the_first_block_and_block_tex_the_chosen_one(self):
        data, _, pix = self.make()
        self.assertEqual((T.parse(data).width, T.parse(data).height), (16, 16))
        for b in T.find_blocks(data):
            tex = T.block_tex(data, b)
            self.assertEqual(tex.tobytes(), data)                  # head/tail are the rest of the file
            self.assertEqual((tex.width, tex.height), (b.width, b.height))
            self.assertEqual([p for p, _, _ in tex.mip_pixels()], pix[b.index])
        other, _, _ = self.make(sizes=((16, 16, 10),))
        with self.assertRaises(T.TexError):                         # a block of another file
            T.block_tex(other, T.find_blocks(data)[1])
        b = T.find_blocks(data)[1]
        with self.assertRaises(T.TexError):                         # right STAR, wrong size field
            T.block_tex(data, b._replace(size=b.size - 16))

    def test_not_a_texture_file(self):
        with self.assertRaises(T.TexError):
            T.find_blocks(b"nothing")


class RegionDxtTest(unittest.TestCase):
    """with_region on flat-colour DXT blocks: every mip pixel is known from the fixture's own model."""
    GREEN = (0, 255, 0)

    def setUp(self):
        self.data, self.where, self.pix = AtlasBlocksTest().make()

    def write(self, bi, rect, colour=GREEN):
        x, y, w, h = rect
        new = bytes(colour + (255,)) * (w * h)
        blk = T.get_block(self.data, index=bi)
        out = T.with_region(self.data, blk, x, y, new, w, h)
        return out, new, blk

    def check(self, bi, rect, colour=GREEN, exact_levels=None):
        out, new, blk = self.write(bi, rect, colour)
        w, h = blk.width, blk.height
        s, e = self.where[bi][1], self.where[bi][1] + self.where[bi][2]
        # the file: same length, everything outside this block byte for byte (other blocks included)
        self.assertEqual(len(out), len(self.data))
        self.assertEqual((out[:s], out[e:]), (self.data[:s], self.data[e:]))
        # natural blocks: only the covering ones may differ
        dims = F.mips(w, h)
        nblocks = sum(max(1, (lw + 3) // 4) * max(1, (lh + 3) // 4) for lw, lh in dims)
        old_n, new_n = F.natural_blocks(self.data[s:e], nblocks), F.natural_blocks(out[s:e], nblocks)
        model = F.model_region(self.pix[bi], dims, rect, new)
        pos = 0
        for lvl, (lw, lh) in enumerate(dims):
            cnt = max(1, (lw + 3) // 4) * max(1, (lh + 3) // 4)
            changed = {i for i in range(cnt) if old_n[pos + i] != new_n[pos + i]}
            self.assertLessEqual(changed, covering(rect, lvl, lw, lh), "level %d touched a block outside" % lvl)
            if model[lvl] != self.pix[bi][lvl]:     # the picture of this level really changes: bytes must too
                self.assertTrue(changed, "level %d was not regenerated" % lvl)
            pos += cnt
        # decoded pixels == the model (exact for <= 2 representable colours, one 565 step for <= 2 others; a block cannot hold more, so beyond that only a sanity bound)
        got = [p for p, _, _ in T.block_tex(out, T.get_block(out, index=bi)).mip_pixels()]
        for lvl, (lw, lh) in enumerate(dims):
            nbx = max(1, (lw + 3) // 4)
            for by in range(max(1, (lh + 3) // 4)):
                for bx in range(nbx):
                    idx = [(y * lw + x) * 4 for y in range(by * 4, min(lh, by * 4 + 4)) for x in range(bx * 4, min(lw, bx * 4 + 4))]
                    colours = {model[lvl][i:i + 4] for i in idx}
                    exact565 = all(c[:3] in EXACT_565 for c in colours)
                    if len(colours) > 2:      # a 4-colour block cannot hold a blend of 3+ colours: nothing to compare
                        continue
                    tol = 0 if exact565 else 8
                    for i in idx:
                        err = max(abs(a - b) for a, b in zip(got[lvl][i:i + 4], model[lvl][i:i + 4]))
                        self.assertLessEqual(err, tol,
                                             "level %d block %d,%d pixel %d off by %d" % (lvl, bx, by, i // 4, err))
        return out, model

    def test_a_block_aligned_rect_changes_exactly_its_blocks_at_level_0(self):
        grey = F.expand565(0x7BEF)                                     # not a colour of the fixture palette
        out, _ = self.check(1, (8, 8, 8, 8), colour=grey)
        s = self.where[1][1]
        old, new = F.natural_blocks(self.data[s:s + self.where[1][2]], 87), F.natural_blocks(out[s:s + self.where[1][2]], 87)
        inside = {2 * 8 + 2, 2 * 8 + 3, 3 * 8 + 2, 3 * 8 + 3}        # blocks (2,2) (3,2) (2,3) (3,3) of the 8x8 grid
        self.assertEqual({i for i in range(64) if old[i] != new[i]}, inside)
        for i in inside:                                              # the fixture's own colours, no decoder involved
            self.assertEqual(new[i][8:12], struct.pack("<HH", 0x7BEF, 0x7BEE))

    def test_neighbours_on_all_four_sides_are_untouched(self):
        self.check(1, (12, 12, 4, 4))
        self.check(1, (0, 0, 4, 4))
        self.check(1, (28, 28, 4, 4))      # the last block of the level
        self.check(1, (0, 28, 32, 4))      # a full-width strip

    def test_rects_that_are_not_aligned_at_the_mips(self):
        for rect in ((4, 4, 4, 4), (4, 12, 8, 4), (20, 4, 12, 20), (8, 8, 24, 8)):
            self.check(1, rect)

    def test_dxt3_and_other_blocks_of_the_same_file(self):
        self.check(2, (16, 8, 8, 16), colour=(255, 255, 0))      # the 64x64 DXT3 block: untouched blocks 0 and 1
        self.check(0, (4, 4, 8, 8), colour=(0, 0, 255))

    def test_the_whole_texture_as_a_region_equals_with_pixels(self):
        for sizes in (((16, 16, 10),), ((32, 16, 10),), ((16, 16, 11),), ((16, 8, 11),), ((12, 12, 10),), ((24, 12, 11),)):
            data, _, _ = AtlasBlocksTest().make(sizes=sizes)
            tex = T.block_tex(data, T.get_block(data, index=0))
            w, h = tex.width, tex.height
            pic = F.pattern(w, h, 4)
            self.assertEqual(tex.with_region(0, 0, pic, w, h).stored, tex.with_pixels(pic).stored)

    def test_region_pixels_reads_any_rectangle_and_level(self):
        data = self.data
        blk = T.get_block(data, index=1)
        tex = T.block_tex(data, blk)
        lvls = self.pix[1]
        self.assertEqual(tex.region_pixels(0, 0, 32, 32), lvls[0])
        for x, y, w, h in ((3, 5, 9, 7), (31, 31, 1, 1), (4, 4, 4, 4)):
            want = b"".join(lvls[0][((y + j) * 32 + x) * 4:((y + j) * 32 + x + w) * 4] for j in range(h))
            self.assertEqual(tex.region_pixels(x, y, w, h), want)
            self.assertEqual(T.region_pixels(data, blk, x, y, w, h), want)
        self.assertEqual(tex.region_pixels(1, 0, 3, 2, level=2), b"".join(
            lvls[2][(j * 8 + 1) * 4:(j * 8 + 4) * 4] for j in range(2)))
        self.assertEqual(tex.region_pixels(0, 0, 1, 1, level=5), lvls[5])
        for bad in ((0, 0, 33, 1, 0), (0, 0, 1, 1, 6), (0, 0, 1, 1, -1), (-1, 0, 1, 1, 0), (0, 0, 0, 1, 0), (0, 0, 9, 1, 2)):
            with self.assertRaises(T.TexError):
                tex.region_pixels(*bad[:4], level=bad[4])

    def test_refusals_leave_the_data_alone(self):
        blk = T.get_block(self.data, index=1)
        ok = bytes(4 * 4 * 4)
        for rect in ((2, 0, 4, 4), (0, 2, 4, 4), (0, 0, 6, 4), (0, 0, 4, 6), (28, 28, 8, 4), (-4, 0, 4, 4), (0, 0, 0, 4)):
            with self.assertRaises(T.TexError):
                T.with_region(self.data, blk, rect[0], rect[1], bytes(max(0, rect[2] * rect[3] * 4)), rect[2], rect[3])
        with self.assertRaises(T.TexError):
            T.with_region(self.data, blk, 0, 0, ok[:-1], 4, 4)
        with self.assertRaises(T.TexError):
            T.with_region(self.data, blk, 0.0, 0, ok, 4, 4)
        with self.assertRaises(T.TexError):
            T.with_region(self.data, blk, True, 0, ok, 4, 4)

    def test_bits16_and_se2_with_mips_are_refused(self):
        data = F.build_hd(4, 4, 6, bytes(2 * 21))
        tex = T.parse(data)
        for call in (lambda: tex.region_pixels(0, 0, 1, 1), lambda: tex.with_region(0, 0, bytes(4), 1, 1)):
            with self.assertRaises(T.TexError):
                call()
        se2 = T.parse(F.build_se2(4, 4, bytes(64), mips_field=4))
        with self.assertRaises(T.TexError):
            se2.with_region(0, 0, bytes(4), 1, 1)


class RegionRgbaTest(unittest.TestCase):
    def tex(self, w, h, fmt=5):
        data, pic = rgba_tex(w, h, fmt=fmt)
        return T.parse(data), pic

    def test_any_rectangle_equals_rewriting_the_whole_picture(self):
        tex, pic = self.tex(16, 16)
        for rect in ((3, 5, 6, 7), (0, 0, 16, 16), (15, 15, 1, 1), (4, 0, 8, 3), (1, 9, 1, 5)):
            x, y, w, h = rect
            new = F.pattern(w, h, 77)
            full = bytearray(pic)
            for j in range(h):
                full[((y + j) * 16 + x) * 4:((y + j) * 16 + x + w) * 4] = new[j * w * 4:(j + 1) * w * 4]
            got = tex.with_region(x, y, new, w, h)
            want = F.hd_rgba_block(F.rgba_chain(bytes(full), 16, 16))        # the fixture's own box chain
            self.assertEqual(got.stored, want, "rect %r" % (rect,))
            self.assertEqual((got.head, got.tail), (tex.head, tex.tail))
            self.assertEqual(got.region_pixels(x, y, w, h), new)

    def test_only_touched_pixels_change_in_every_level(self):
        tex, pic = self.tex(16, 16)
        new = F.pattern(4, 4, 9)
        got = tex.with_region(8, 8, new, 4, 4)
        before, after = tex.mip_pixels(), got.mip_pixels()
        for lvl, ((a, lw, lh), (b, _, _)) in enumerate(zip(before, after)):
            f = 1 << lvl
            for py in range(lh):
                for px in range(lw):
                    touched = px * f < 12 and (px + 1) * f > 8 and py * f < 12 and (py + 1) * f > 8
                    same = a[(py * lw + px) * 4:(py * lw + px) * 4 + 4] == b[(py * lw + px) * 4:(py * lw + px) * 4 + 4]
                    if not touched:
                        self.assertTrue(same, "level %d pixel %d,%d" % (lvl, px, py))

    def test_non_square_texture_matches_the_whole_picture_path(self):
        tex = T.parse(F.build_hd(16, 8, 3, bytes(4 * (128 + 32 + 8 + 2 + 1))))   # levels 16x8 .. 2x1 .. 1x1
        pic = F.pattern(16, 8, 5)
        part = F.pattern(5, 3, 2)
        full = bytearray(pic)
        for j in range(3):
            full[((2 + j) * 16 + 7) * 4:((2 + j) * 16 + 12) * 4] = part[j * 20:(j + 1) * 20]
        base = tex.with_pixels(pic)
        self.assertEqual(base.with_region(7, 2, part, 5, 3).stored, tex.with_pixels(bytes(full)).stored)

    def test_se2_raw_bgra_is_patched_through_the_pixels(self):
        bgra = bytes((x * 7) & 255 for x in range(8 * 8 * 4))
        tex = T.parse(F.build_se2(8, 8, bgra))
        new = F.pattern(3, 2, 1)
        got = tex.with_region(2, 3, new, 3, 2)
        self.assertEqual(got.region_pixels(2, 3, 3, 2), new)
        full = bytearray(tex.pixels()[0])
        for j in range(2):
            full[((3 + j) * 8 + 2) * 4:((3 + j) * 8 + 5) * 4] = new[j * 12:(j + 1) * 12]
        self.assertEqual(got.pixels()[0], bytes(full))
        self.assertEqual((got.head, got.tail), (tex.head, tex.tail))


class EncoderTest(unittest.TestCase):
    def roundtrip(self, alphas, rgb=(200, 100, 50)):
        px = [rgb + (a,) for a in alphas]
        return list(image.decode(T.encode_block(px, "DXT5"), 4, 4, "DXT5")[3::4])

    def test_alpha_sets_the_game_uses_come_back_exactly(self):
        # mode "6": 18 and 255 as endpoints, 112 and 207 are interpolants, 0 and 255 exact
        a = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 18, 18, 112, 207, 255]
        self.assertEqual(self.roundtrip(a), a)
        # mode "8": endpoints 250 and 10, interpolants (6-i)*250+(i+1)*10 over 7: 215 181 147 112 78 44
        b = [250, 10, 215, 181, 147, 112, 78, 44] * 2
        self.assertEqual(self.roundtrip(b), b)
        for flat in (0, 255, 77):
            self.assertEqual(self.roundtrip([flat] * 16), [flat] * 16)

    def test_alpha_error_is_small_for_a_soft_edge(self):
        edge = [min(255, 60 * x + 15 * y) for y in range(4) for x in range(4)]
        self.assertLessEqual(max(abs(p - q) for p, q in zip(self.roundtrip(edge), edge)), 18)

    def test_a_two_colour_block_is_exact_in_dxt5_and_dxt3(self):
        px = [((255, 0, 0, 255) if i % 3 else (0, 0, 255, 255)) for i in range(16)]
        got = image.decode(T.encode_block(px, "DXT5"), 4, 4, "DXT5")
        self.assertEqual(got, bytes(v for p in px for v in p))
        three = T.encode_block(px, "DXT3")
        self.assertEqual(three[:8], image.encode_block(px, "DXT3")[:8])          # the 4-bit alpha part is image.py's
        self.assertEqual(image.decode(three, 4, 4, "DXT3"), bytes(v for p in px for v in p))


class GroTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_build_is_deterministic_stored_and_readable(self):
        entries = [("Content/SeriousSamHD/Textures/Menu/A_button.tex", b"aaa" * 50), ("b.bin", b"")]
        one, two = gro.build(entries), gro.build(entries)
        self.assertEqual(one, two)
        self.assertEqual(hashlib.sha256(one).digest(), hashlib.sha256(two).digest())
        with open(self.path("z.gro"), "wb") as f:
            f.write(one)
        with zipfile.ZipFile(self.path("z.gro")) as z:
            self.assertIsNone(z.testzip())
            for i in z.infolist():
                self.assertEqual((i.date_time, i.compress_type, i.flag_bits, i.create_system, i.extra), (
                    (1980, 1, 1, 0, 0, 0), zipfile.ZIP_STORED, 0, 0, b""))
            self.assertEqual(z.namelist(), [e[0] for e in entries])      # given order kept
        self.assertEqual(one[:4], b"PK\x03\x04")

    def test_a_built_overlay_holds_a_reencoded_tex(self):
        original, _ = rgba_tex(8, 8, stamp=TSE)
        name = "Content/SeriousSamHD_TSE/Textures/Menu/A_button.tex"
        base = self.path("All.gro")
        with open(base, "wb") as f:
            f.write(gro.build([(name, original)], deflate=True))
        got = gro.read_entry(base, name.lower())                           # case-insensitive
        self.assertEqual(got, original)
        overlay = gro.build([(name, T.reencode(got, F.pattern(8, 8, 3)))])
        with open(self.path("Zz_overlay.gro"), "wb") as f:
            f.write(overlay)
        real, data, src = gro.read_from_layers([base, self.path("Zz_overlay.gro")], name)
        self.assertEqual((real, src), (name, self.path("Zz_overlay.gro")))   # the later layer wins
        self.assertEqual(T.decode(data)[0], F.pattern(8, 8, 3))
        real, data, src = gro.read_from_layers([self.path("Zz_overlay.gro"), base], name)
        self.assertEqual(src, base)                                          # order is the caller's
        self.assertIsNone(gro.read_from_layers([base], "nope.tex"))

    def test_listing_and_limits(self):
        with open(self.path("a.gro"), "wb") as f:
            f.write(gro.build([("x/Big.bin", b"\0" * 5000)], deflate=True))
        (name, size, csize, method), = gro.list_entries(self.path("a.gro"))
        self.assertEqual((name, size, method), ("x/Big.bin", 5000, zipfile.ZIP_DEFLATED))
        self.assertLess(csize, size)
        with self.assertRaises(gro.GroError):
            gro.read_entry(self.path("a.gro"), "x/big.bin", max_bytes=100)
        self.assertIsNone(gro.read_entry(self.path("a.gro"), "missing"))

    def test_bad_names_are_refused(self):
        for bad in ("", "/abs", "a/../b", "a\\b", "café.tex"):
            with self.assertRaises(gro.GroError):
                gro.build([(bad, b"x")])
        with self.assertRaises(gro.GroError):
            gro.build([("A.tex", b"1"), ("a.TEX", b"2")])


if __name__ == "__main__":
    unittest.main()
