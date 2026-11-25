"""Croteam .gro archives: a plain PKZIP (stdlib zipfile is enough), plus a deterministic small writer.

Serious Sam HD (41000, 41010) and Serious Sam 2 (204340) keep their content in `.gro` files that are
ordinary ZIPs: no encryption (flag bit 0 = 0), method 8 (deflate) or 0 (stored), names with forward
slashes like `Content/SeriousSamHD/Textures/Menu/A_button.tex`. The engine mounts every .gro of the
game folder in alphabetical order and a LATER archive wins for a name it also has, which is what makes
an added overlay archive possible without touching the originals (HD builds also append `_<build>`
numbers to the names of patch archives, see the game pages).

Reading: `read_entry` opens the archive (zipfile reads only the central directory, so a 3 GB .gro is
fine), finds the name case-insensitively, refuses entries larger than `max_bytes` and lets zipfile check
the CRC-32. Writing: `build` returns the bytes of a NEW small archive. They are deterministic so a
hash of the overlay can be pinned: STORED (no zlib version in the output), a fixed 1980-01-01 time stamp,
create_system 0, no extra fields, entries in the order given, ASCII names only. Pass `deflate=True`
only if size matters more than a stable hash across zlib versions.
"""
import io
import os
import zipfile
import zlib

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


class GroError(ValueError):
    pass


def list_entries(path):
    """[(name, file_size, compressed_size, method)] in archive order."""
    with zipfile.ZipFile(path) as z:
        return [(i.filename, i.file_size, i.compress_size, i.compress_type) for i in z.infolist()]


def find_name(names, wanted):
    """The archive's own spelling of `wanted` (case-insensitive, / or \\), or None."""
    key = wanted.replace("\\", "/").lower()
    for n in names:
        if n.replace("\\", "/").lower() == key:
            return n
    return None


def read_entry(path, name, max_bytes=DEFAULT_MAX_BYTES):
    """Bytes of one entry, or None when the archive does not have it. CRC is verified."""
    with zipfile.ZipFile(path) as z:
        real = find_name(z.namelist(), name)
        if real is None:
            return None
        info = z.getinfo(real)
        if info.flag_bits & 0x1:
            raise GroError("%s: %s is encrypted" % (path, real))
        if info.file_size > max_bytes:
            raise GroError("%s: %s is %d bytes (limit %d)" % (path, real, info.file_size, max_bytes))
        return z.read(info)


def read_from_layers(paths, name, max_bytes=DEFAULT_MAX_BYTES):
    """The winning copy of `name` across archives given in LOAD order (the last one that has it wins).

    Returns (real name, bytes, path of the archive) or None."""
    found = None
    for p in paths:
        with zipfile.ZipFile(p) as z:
            real = find_name(z.namelist(), name)
        if real is not None:
            found = (real, p)
    if found is None:
        return None
    return found[0], read_entry(found[1], found[0], max_bytes), found[1]


class Layers:
    """Several archives opened ONCE, in LOAD order: the last one that has a name wins.

    `read_from_layers` re-opens every archive for every name; a status poll that checks 6-20 entries across 14
    archives (one of them 5000 names long) would parse the same directories again and again. Use as a context
    manager; archives that cannot be opened raise `GroError` (a damaged .gro is a refusal, never a guess).
    """

    def __init__(self, paths):
        self.paths = list(paths)
        self._zips = []
        self._index = {}

    def __enter__(self):
        try:
            for i, path in enumerate(self.paths):
                try:
                    z = zipfile.ZipFile(path)
                except (zipfile.BadZipFile, OSError) as exc:
                    raise GroError("%s: cannot be read (%s)" % (os.path.basename(path), exc))
                self._zips.append(z)
                for info in z.infolist():
                    self._index[info.filename.replace("\\", "/").lower()] = (i, info)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *exc):
        for z in self._zips:
            z.close()
        self._zips = []
        return False

    def read(self, name, max_bytes=DEFAULT_MAX_BYTES):
        """(real name, bytes, path of the winning archive) or None. CRC is verified by zipfile."""
        hit = self._index.get(name.replace("\\", "/").lower())
        if hit is None:
            return None
        i, info = hit
        if info.flag_bits & 0x1:
            raise GroError("%s: %s is encrypted" % (os.path.basename(self.paths[i]), info.filename))
        if info.file_size > max_bytes:
            raise GroError("%s: %s is %d bytes (limit %d)" % (os.path.basename(self.paths[i]), os.path.basename(info.filename), info.file_size, max_bytes))
        try:
            return info.filename, self._zips[i].read(info), self.paths[i]
        except (zipfile.BadZipFile, OSError, EOFError, zlib.error) as exc:
            raise GroError("%s: %s cannot be read (%s)" % (os.path.basename(self.paths[i]), os.path.basename(info.filename), exc))


def build(entries, deflate=False):
    """Bytes of a new .gro holding `entries` = [(name, data)], deterministic (see module docstring)."""
    seen = set()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in entries:
            if not name or name.startswith("/") or ".." in name.split("/") or "\\" in name:
                raise GroError("bad entry name %r" % (name,))
            try:
                name.encode("ascii")
            except UnicodeEncodeError:
                raise GroError("non-ASCII entry name %r" % (name,))
            if name.lower() in seen:
                raise GroError("duplicate entry %r" % (name,))
            seen.add(name.lower())
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED
            info.create_system = 0
            info.external_attr = 0
            z.writestr(info, bytes(data))
    return buf.getvalue()
