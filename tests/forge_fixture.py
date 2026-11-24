"""Builds small synthetic Scimitar forges (same layout as Assassin's Creed's DataPC.forge)."""
import struct

from button_glyphs import image
from button_glyphs.forge import (
    ALIGN, COMP_MAGIC, FILEDATA_HEADER, FILEDATA_SIZE_FIELD, NAME_ENTRY_SIZE, adler32_0,
    lzo1x_literal,
)
from button_glyphs.profiles.forge_glyphs import ForgeGlyphProfile

TEXTURE_TYPE = 0xA2B7E917
TEX_ID = 0x1234
OTHER_ID = 0x99
CHUNK = 0x8000


def block(data, compress=True):
    chunks = [data[i:i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]
    out = bytearray(COMP_MAGIC + struct.pack("<HBHHH", 1, 1, CHUNK, CHUNK, len(chunks)))
    stored = []
    for i, c in enumerate(chunks):
        # alternate LZO-compressed and stored chunks, like the game files do
        raw = lzo1x_literal(c) if compress and i % 2 == 0 else c
        stored.append(raw)
        out += struct.pack("<HH", len(c), len(raw))
    for raw in stored:
        out += struct.pack("<I", adler32_0(raw)) + raw
    return bytes(out)


def resource(rtype, fid, name, body):
    nm = name.encode()
    tail = struct.pack("<B", 0) + struct.pack("<II", fid, rtype) + body
    return struct.pack("<III", rtype, len(tail) + 0, len(nm)) + nm + tail


def texture_resource(fid, name, w, h, fmt_code, pixels):
    head = struct.pack("<4I", w, h, 1, fmt_code) + b"\x01\x00\x00\x00" * 10
    body = head + bytes.fromhex("e97f2313") + struct.pack("<I", len(pixels)) + pixels + b"\x02" * 61
    return resource(TEXTURE_TYPE, fid, name, body)


def glyph_texture(colours):
    """64x64 DXT3 with filled coloured squares in the profile boxes."""
    w = h = 64
    rgba = bytearray(w * h * 4)
    for (x0, y0, x1, y1), col in colours:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                rgba[(y * w + x) * 4:(y * w + x) * 4 + 4] = bytes(col + (255,))
    out = bytearray()
    for by in range(h // 4):
        for bx in range(w // 4):
            out += image.encode_block(image.block_pixels(bytes(rgba), w, bx, by), "DXT3")
    return bytes(out)


BOXES = {"head": (4, 4, 19, 19), "legs": (36, 36, 51, 51)}
COLOURS = [(BOXES["head"], (250, 210, 0)), (BOXES["legs"], (60, 180, 110))]


def datafile_payload(variant=0, compress=True):
    tex = texture_resource(TEX_ID, "Glyphs_Map", 64, 64, 4, glyph_texture(COLOURS))
    filler = resource(0x55, OTHER_ID, "Filler", bytes((i * 7 + variant) & 255 for i in range(70000)))
    resources = [(OTHER_ID, filler), (TEX_ID, tex)]
    table = struct.pack("<H", len(resources)) + b"".join(
        struct.pack("<II", fid, len(r)) for fid, r in resources)
    data = b"".join(r for _, r in resources)
    return block(table, compress) + block(data, compress)


def build_forge(path, variant=0, compress=True, entry="Game Bootstrap Settings"):
    names = ["GlobalMetaFile", entry, "Game Fix"]
    payloads = [b"meta" * 10, datafile_payload(variant, compress), block(b"fix" * 5000)]
    n = len(names)
    hdr_off = 0x40
    block_off = hdr_off + 0x28
    idx_off = block_off + 0x30
    names_off = idx_off + 16 * n
    names_end = names_off + NAME_ENTRY_SIZE * n
    data_off = (names_end + ALIGN - 1) // ALIGN * ALIGN
    out = bytearray(data_off)
    out[0:9] = b"scimitar\0"
    struct.pack_into("<iq", out, 9, 25, hdr_off)
    struct.pack_into("<iiqqiiq", out, hdr_off, n, 1, 0, -1, n, 1, block_off)
    struct.pack_into("<iiqqiiqq", out, block_off, n, 1, idx_off, -1, 0, n - 1, names_off, names_end)
    for i, (name, payload) in enumerate(zip(names, payloads)):
        off = len(out)
        header = bytearray(FILEDATA_HEADER)
        header[0:8] = b"FILEDATA"
        header[8:8 + len(name)] = name.encode()
        struct.pack_into("<I", header, FILEDATA_SIZE_FIELD - 4, 0x800 + i)
        struct.pack_into("<I", header, FILEDATA_SIZE_FIELD, len(payload))
        out += header + payload
        out += b"\0" * ((-len(out)) % ALIGN)
        struct.pack_into("<qII", out, idx_off + 16 * i, off, 0x800 + i, len(payload))
        ne = bytearray(NAME_ENTRY_SIZE)
        struct.pack_into("<I", ne, 0, len(payload))
        ne[44:44 + len(name)] = name.encode()
        out[names_off + NAME_ENTRY_SIZE * i:names_off + NAME_ENTRY_SIZE * (i + 1)] = ne
    with open(path, "wb") as f:
        f.write(out)


def test_profile(appid="15100"):
    return ForgeGlyphProfile(
        appid=appid,
        name="Test Game",
        file="DataPC.forge",
        datafile="Game Bootstrap Settings",
        roles=[("head", "Head"), ("legs", "Legs")],
        textures=[{"id": TEX_ID, "label": "glyph atlas", "width": 64, "height": 64,
                   "format": "DXT3", "boxes": dict(BOXES)}],
        styles={"ps": {"head": "ps/outline-green-triangle", "legs": "ps/outline-blue-cross"},
                "xbox": {"head": "xbox/y-filled-yellow", "legs": "xbox/a-filled-green"}},
    )
