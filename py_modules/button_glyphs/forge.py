"""Ubisoft Scimitar .forge archives (Assassin's Creed 1 era, forge version 25).

Layout (all little-endian):
  "scimitar\\0", i32 version, i64 offset of the file-list header
  header -> chain of blocks: [i32 count, i32, i64 index_off, i64 next_block,
                              i32 first, i32 last, i64 names_off, i64 names_end]
  index entry (16 B): i64 offset, u32 file_id, u32 payload size
  name entry (188 B): u32 payload size, ..., char name[128] at +44
  entry data: 0x1B8-byte FILEDATA header (payload size u32 at +0x18B), then the payload:
  a resource-table block followed by a resource-data block.

Blocks start with magic 0x1004FA9957FBAA33, hold up to 32 KiB chunks, each LZO1X
compressed (or stored raw when compressed size == uncompressed size) and preceded
by an Adler-32 (initial value 0) of the stored bytes.

Stdlib only: this runs inside the Decky backend.
"""
import os
import struct
import zlib

COMP_MAGIC = b"\x33\xaa\xfb\x57\x99\xfa\x04\x10"
NAME_ENTRY_SIZE = 188
FILEDATA_HEADER = 0x1B8
FILEDATA_SIZE_FIELD = 0x18B
ALIGN = 0x800


class ForgeError(Exception):
    pass


# --------------------------------------------------------------------------- LZO1X


def lzo1x_decompress(src, out_len=None):
    """Port of the Linux kernel lzo1x_decompress_safe (without the lzo-rle extension)."""
    src = memoryview(src)
    n = len(src)
    out = bytearray()
    ip = 0
    state = 0

    def copy_match(m_pos, length):
        if m_pos < 0:
            raise ForgeError("lzo: lookbehind overrun")
        if len(out) - m_pos >= length:
            out.extend(out[m_pos:m_pos + length])
        else:
            for _ in range(length):
                out.append(out[m_pos])
                m_pos += 1

    if n and src[0] > 17:
        t = src[0] - 17
        ip = 1
        out.extend(src[ip:ip + t])
        ip += t
        state = t if t < 4 else 4

    while True:
        if ip >= n:
            raise ForgeError("lzo: input overrun")
        t = src[ip]
        ip += 1
        if t < 16:
            if state == 0:
                if t == 0:
                    start = ip
                    while src[ip] == 0:
                        ip += 1
                    t = (ip - start) * 255 + 15 + src[ip]
                    ip += 1
                t += 3
                out.extend(src[ip:ip + t])
                ip += t
                state = 4
                continue
            if state != 4:
                nxt = t & 3
                m_pos = len(out) - 1 - (t >> 2) - (src[ip] << 2)
                ip += 1
                copy_match(m_pos, 2)
                state = nxt
                if nxt:
                    out.extend(src[ip:ip + nxt])
                    ip += nxt
                continue
            nxt = t & 3
            m_pos = len(out) - (1 + 0x0800) - (t >> 2) - (src[ip] << 2)
            ip += 1
            length = 3
        elif t >= 64:
            nxt = t & 3
            m_pos = len(out) - 1 - ((t >> 2) & 7) - (src[ip] << 3)
            ip += 1
            length = (t >> 5) + 1
        elif t >= 32:
            length = (t & 31) + 2
            if length == 2:
                start = ip
                while src[ip] == 0:
                    ip += 1
                length += (ip - start) * 255 + 31 + src[ip]
                ip += 1
            v = src[ip] | (src[ip + 1] << 8)
            ip += 2
            m_pos = len(out) - 1 - (v >> 2)
            nxt = v & 3
        else:
            m_pos = len(out) - ((t & 8) << 11)
            length = (t & 7) + 2
            if length == 2:
                start = ip
                while src[ip] == 0:
                    ip += 1
                length += (ip - start) * 255 + 7 + src[ip]
                ip += 1
            v = src[ip] | (src[ip + 1] << 8)
            ip += 2
            m_pos -= v >> 2
            nxt = v & 3
            if m_pos == len(out):
                break
            m_pos -= 0x4000
        copy_match(m_pos, length)
        state = nxt
        if nxt:
            out.extend(src[ip:ip + nxt])
            ip += nxt

    if out_len is not None and len(out) != out_len:
        raise ForgeError("lzo: size mismatch %d != %d" % (len(out), out_len))
    return bytes(out)


def lzo1x_literal(data):
    """A valid LZO1X stream that stores `data` as one literal run (used by tests)."""
    out = bytearray()
    n = len(data)
    if n == 0:
        return b"\x11\x00\x00"
    if n <= 238:
        out.append(17 + n)
    else:
        out.append(0)
        rest = n - 18
        while rest > 255:
            out.append(0)
            rest -= 255
        out.append(rest)
    out += data
    out += b"\x11\x00\x00"
    return bytes(out)


def adler32_0(data):
    return zlib.adler32(data, 0)


# --------------------------------------------------------------------------- blocks


class Block:
    """A compressed block parsed lazily: chunks keep their stored bytes until touched."""

    def __init__(self, buf, off):
        if bytes(buf[off:off + 8]) != COMP_MAGIC:
            raise ForgeError("bad block magic at %#x" % off)
        self.head = bytes(buf[off + 8:off + 17])
        count = struct.unpack_from("<H", buf, off + 15)[0]
        p = off + 17
        sizes = []
        for _ in range(count):
            sizes.append(struct.unpack_from("<HH", buf, p))
            p += 4
        self.chunks = []  # [usz, csz, checksum, stored bytes]
        for usz, csz in sizes:
            chk = struct.unpack_from("<I", buf, p)[0]
            self.chunks.append([usz, csz, chk, bytes(buf[p + 4:p + 4 + csz])])
            p += 4 + csz
        self.end = p
        self.starts = []
        total = 0
        for chunk in self.chunks:
            self.starts.append(total)
            total += chunk[0]
        self.size = total

    def chunk_data(self, i):
        usz, csz, _, raw = self.chunks[i]
        return raw if usz == csz else lzo1x_decompress(raw, usz)

    def _overlapping(self, start, end):
        for i, s in enumerate(self.starts):
            usz = self.chunks[i][0]
            if s < end and s + usz > start:
                yield i, s, usz

    def read(self, start, length):
        out = bytearray()
        for i, s, usz in self._overlapping(start, start + length):
            data = self.chunk_data(i)
            out += data[max(start, s) - s:min(start + length, s + usz) - s]
        return bytes(out)

    def patch(self, start, new):
        """Overwrite uncompressed bytes; every touched chunk is re-stored uncompressed."""
        end = start + len(new)
        for i, s, usz in self._overlapping(start, end):
            data = bytearray(self.chunk_data(i))
            a, b = max(start, s), min(end, s + usz)
            data[a - s:b - s] = new[a - start:b - start]
            data = bytes(data)
            self.chunks[i] = [usz, usz, adler32_0(data), data]

    def bad_checksums(self):
        return sum(1 for _, _, chk, raw in self.chunks if adler32_0(raw) != chk)

    def tobytes(self):
        out = bytearray(COMP_MAGIC + self.head)
        for usz, csz, _, _ in self.chunks:
            out += struct.pack("<HH", usz, csz)
        for _, _, chk, raw in self.chunks:
            out += struct.pack("<I", chk) + raw
        return bytes(out)


class DataFile:
    """Payload of one forge entry: resource table block + resource data block."""

    def __init__(self, payload):
        self.table_block = Block(payload, 0)
        self.data_block = Block(payload, self.table_block.end)
        self.tail = bytes(payload[self.data_block.end:])
        table = self.table_block.read(0, self.table_block.size)
        count = struct.unpack_from("<H", table)[0]
        self.resources = {}
        pos = 0
        for i in range(count):
            fid, size = struct.unpack_from("<II", table, 2 + 8 * i)
            self.resources[fid] = (pos, size)
            pos += size

    def read_resource(self, fid):
        if fid not in self.resources:
            raise ForgeError("resource %#x not found" % fid)
        start, size = self.resources[fid]
        return self.data_block.read(start, size)

    def patch_resource(self, fid, rel, new):
        start, size = self.resources[fid]
        if rel < 0 or rel + len(new) > size:
            raise ForgeError("patch outside resource %#x" % fid)
        self.data_block.patch(start + rel, new)

    def tobytes(self):
        return self.table_block.tobytes() + self.data_block.tobytes() + self.tail


# --------------------------------------------------------------------------- archive


class Entry:
    __slots__ = ("index", "offset", "file_id", "size", "name", "slot_off", "name_off", "name_size")


class Forge:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            head = f.read(0x40)
            if head[:8] != b"scimitar":
                raise ForgeError("not a scimitar forge: %s" % path)
            self.version, hdr_off = struct.unpack_from("<iq", head, 9)
            f.seek(hdr_off)
            first_block = struct.unpack_from("<q", f.read(0x28), 0x20)[0]
            self.entries = []
            off, guard = first_block, 0
            while off != -1:
                guard += 1
                if guard > 1000:
                    raise ForgeError("block chain loops")
                f.seek(off)
                _, _, idx, nxt, first, last, noff, _ = struct.unpack("<iiqqiiqq", f.read(0x30))
                n = last - first + 1
                f.seek(idx)
                ib = f.read(16 * n)
                f.seek(noff)
                nb = f.read(NAME_ENTRY_SIZE * n)
                for i in range(n):
                    o, fid, sz = struct.unpack_from("<qII", ib, 16 * i)
                    if o == 0 and sz == 0:
                        continue
                    e = Entry()
                    e.index = first + i
                    e.offset, e.file_id, e.size = o, fid, sz
                    e.slot_off = idx + 16 * i
                    e.name_off = noff + NAME_ENTRY_SIZE * i
                    e.name_size = struct.unpack_from("<I", nb, NAME_ENTRY_SIZE * i)[0]
                    raw = nb[NAME_ENTRY_SIZE * i + 44:NAME_ENTRY_SIZE * i + 172]
                    e.name = raw.split(b"\0")[0].decode("latin-1")
                    self.entries.append(e)
                off = nxt

    def find(self, name):
        for e in self.entries:
            if e.name == name:
                return e
        raise ForgeError("entry %r not found" % name)

    def read_payload(self, entry):
        with open(self.path, "rb") as f:
            f.seek(entry.offset + FILEDATA_HEADER)
            data = f.read(entry.size)
        if len(data) != entry.size:
            raise ForgeError("truncated entry %r" % entry.name)
        return data

    def load(self, name):
        return DataFile(self.read_payload(self.find(name)))


def read_entry_header(path, entry):
    with open(path, "rb") as f:
        f.seek(entry.offset)
        return f.read(FILEDATA_HEADER)


def append_blob(f, header, payload):
    """Append header+payload at the next ALIGN boundary of the open file; return its offset."""
    f.seek(0, os.SEEK_END)
    eof = f.tell()
    off = (eof + ALIGN - 1) // ALIGN * ALIGN
    f.write(b"\0" * (off - eof))
    f.write(header)
    f.write(payload)
    f.write(b"\0" * ((-f.tell()) % ALIGN))
    return off


def set_slot(f, entry, offset, size, name_size):
    """Repoint an entry in the index (and the size kept in its name record)."""
    f.seek(entry.slot_off)
    f.write(struct.pack("<qII", offset, entry.file_id, size))
    f.seek(entry.name_off)
    f.write(struct.pack("<I", name_size))


def with_payload_size(header, size):
    h = bytearray(header)
    struct.pack_into("<I", h, FILEDATA_SIZE_FIELD, size)
    return bytes(h)


def append_entry_payload(path, entry, payload):
    """Write `payload` for `entry` at the end of the file and repoint the index at it.

    The original bytes stay where they were, so a crash never leaves a half-written
    entry behind: until the index is rewritten, the old data is still the live one.
    """
    header = with_payload_size(read_entry_header(path, entry), len(payload))
    with open(path, "r+b") as f:
        new_off = append_blob(f, header, payload)
        f.flush()
        os.fsync(f.fileno())
        set_slot(f, entry, new_off, len(payload), len(payload))
        f.flush()
        os.fsync(f.fileno())
    return new_off
