"""CRI .cpk archives and the Dimps .emb texture bundles inside them (read only, stdlib).

A CPK starts with a "CPK " @UTF table (header), whose TocOffset points at a "TOC " chunk
holding one row per file (DirName, FileName, FileSize, ExtractSize, FileOffset). Files
are stored as-is when FileSize == ExtractSize; offsets are relative to
min(TocOffset, ContentOffset).

An .emb bundle is "#EMB", a table of (offset, size) pairs and a table of name offsets;
each member is a plain DDS file.
"""
import struct

SCALARS = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "Q", 7: "q", 8: "f"}


class CpkError(Exception):
    pass


def _utf_table(buf, off=0):
    """Rows of the @UTF table starting at buf[off:] (a list of dicts)."""
    if buf[off:off + 4] != b"@UTF":
        raise CpkError("@UTF table expected")
    base = off + 8
    rows_off, str_off, data_off, _name, ncol, rowlen, nrows = struct.unpack_from(">IIIIHHI", buf, base)

    def cstr(o):
        s = base + str_off + o
        return buf[s:buf.index(b"\0", s)].decode("utf-8", "replace")

    def value(p, ty):
        if ty in SCALARS:
            fmt = ">" + SCALARS[ty]
            return struct.unpack_from(fmt, buf, p)[0], p + struct.calcsize(fmt)
        if ty == 0xA:
            return cstr(struct.unpack_from(">I", buf, p)[0]), p + 4
        if ty == 0xB:
            o, n = struct.unpack_from(">II", buf, p)
            return (base + data_off + o, n), p + 8
        raise CpkError("unknown @UTF column type %d" % ty)

    p = base + 24
    cols = []
    for _ in range(ncol):
        flags = buf[p]
        name = cstr(struct.unpack_from(">I", buf, p + 1)[0])
        p += 5
        storage, ty = flags & 0xF0, flags & 0x0F
        const = None
        if storage == 0x30:
            const, p = value(p, ty)
        cols.append((name, storage, ty, const))
    rows = []
    for r in range(nrows):
        q = base + rows_off + r * rowlen
        row = {}
        for name, storage, ty, const in cols:
            if storage == 0x50:
                row[name], q = value(q, ty)
            else:
                row[name] = const
        rows.append(row)
    return rows


def read_toc(f):
    """{"dir/file": (absolute offset, size, extract size)} of an open CPK."""
    f.seek(0)
    head = f.read(0x800)
    if head[:4] != b"CPK ":
        raise CpkError("not a CPK file")
    h = _utf_table(head, 16)[0]
    toc_off, toc_size, content = h["TocOffset"], h["TocSize"], h["ContentOffset"]
    if not toc_off or not toc_size:
        raise CpkError("CPK without a TOC")
    base = min(toc_off, content) if content else toc_off
    f.seek(toc_off)
    toc = f.read(toc_size + 16)
    if toc[:4] != b"TOC ":
        raise CpkError("TOC chunk not found")
    files = {}
    for r in _utf_table(toc, 16):
        files["%s/%s" % (r["DirName"], r["FileName"])] = (
            base + r["FileOffset"], r["FileSize"], r["ExtractSize"])
    return files


def read_emb(f, offset, size):
    """[(name, absolute offset, size)] of the DDS members of the .emb at `offset`."""
    f.seek(offset)
    head = f.read(0x20)
    if head[:4] != b"#EMB":
        raise CpkError("#EMB bundle expected")
    count, = struct.unpack_from("<I", head, 12)
    table, names = struct.unpack_from("<II", head, 0x18)
    if not count or count > 4096 or not table or table >= size or not names or names >= size:
        raise CpkError("unusual #EMB header")
    f.seek(offset + table)
    pairs = struct.unpack("<%dI" % (2 * count), f.read(8 * count))
    f.seek(offset + names)
    name_offs = struct.unpack("<%dI" % count, f.read(4 * count))
    out = []
    for i in range(count):
        member = offset + table + i * 8 + pairs[2 * i]
        if name_offs[i] >= size or pairs[2 * i + 1] > size:
            raise CpkError("#EMB member out of range")
        f.seek(offset + name_offs[i])
        name = f.read(96).split(b"\0")[0].decode("latin1")
        out.append((name, member, pairs[2 * i + 1]))
    return out


def read_dds_header(f, offset):
    """(width, height, mip count, fourcc) of the DDS at `offset`."""
    f.seek(offset)
    h = f.read(128)
    if h[:4] != b"DDS ":
        raise CpkError("DDS expected")
    height, width = struct.unpack_from("<II", h, 12)
    mips, = struct.unpack_from("<I", h, 28)
    return width, height, mips, h[84:88].decode("latin1")
