"""The marker we leave at the end of every game file we modify.

The last ALIGN bytes of a patched file hold a JSON record (style, profile revision and
whatever is needed to undo the change). No marker means the file is an original: a fresh
install, a Steam update or "Verify integrity of game files".
"""
import json
import os
import struct

from .forge import ALIGN

TRAILER_MAGIC = b"BUTTONGLYPHS\x01"


def read_trailer(path):
    """The trailer dict of a file we patched, or None for any other file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size < ALIGN:
                return None
            f.seek(size - ALIGN)
            tail = f.read(ALIGN)
    except OSError:
        return None
    if not tail.startswith(TRAILER_MAGIC):
        return None
    try:
        n = struct.unpack_from("<I", tail, len(TRAILER_MAGIC))[0]
        start = len(TRAILER_MAGIC) + 4
        return json.loads(tail[start:start + n].decode("utf-8"))
    except (ValueError, struct.error):
        return None


def write_trailer(path, info, at=None):
    """Write the marker at the end of the file, or at offset `at` (a multiple of ALIGN) in ONE write
    that also extends the file to at + ALIGN. The bytes are the same either way."""
    body = json.dumps(info, sort_keys=True).encode("utf-8")
    block = TRAILER_MAGIC + struct.pack("<I", len(body)) + body
    if len(block) > ALIGN:
        raise ValueError("trailer too large")
    with open(path, "r+b") as f:
        if at is None:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.write(b"\0" * ((-size) % ALIGN))
        else:
            f.seek(0, os.SEEK_END)
            if at % ALIGN or at < f.tell():          # never over existing bytes, always on the block grid
                raise ValueError("trailer offset %d must be a multiple of %d and not before the end of the file" % (at, ALIGN))
            f.seek(at)
        f.write(block + b"\0" * (ALIGN - len(block)))
        f.flush()
        os.fsync(f.fileno())
