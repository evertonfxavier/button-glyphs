"""Entry-level patching of Scimitar forges: append the new entry, keep the original.

Big games spread their button art over many archives of hundreds of MB. Instead of
copying each archive, a patched entry is written at the end of the file and only its
16-byte index slot is repointed; the original entry stays where it was. Undoing the
change is then exact: put the slot back and cut the file to its original size, which
gives back the very same bytes Steam installed.

State lives in a trailer (see trailer.py) at the end of the file:

  kind          "entries"
  orig_size     file size before we touched it
  patched_size  file size once we finished (trailer included)
  entries       [{name, slot_off, name_off, orig:[offset, file_id, size],
                  orig_name_size, new:[offset, size]}]

Write order matters for power loss: blob -> trailer -> slot (and the reverse to undo:
slot -> truncate). At every instant the file is either fully original, or recoverable
from the trailer.
"""
import os
import shutil
import struct
import time

from .forge import (
    ALIGN, Forge, ForgeError, append_blob, read_entry_header, set_slot, with_payload_size,
)
from .trailer import read_trailer, write_trailer

SLOT = struct.Struct("<qII")
MIN_FREE = 64 << 20

ORIGINAL = "original"    # no trailer: untouched (or restored) file
PATCHED = "patched"      # trailer matches the index: our change is live
PARTIAL = "partial"      # we crashed before repointing the index: blob is dead weight
STRANGE = "strange"      # trailer present but the file no longer matches it (game update?)
LEGACY = "legacy"        # trailer of the old whole-file strategy


def _read_slot(f, off):
    f.seek(off)
    return SLOT.unpack(f.read(SLOT.size))


def inspect(path):
    """Classify `path`. Returns (state, trailer-or-None)."""
    trailer = read_trailer(path)
    if trailer is None:
        return ORIGINAL, None
    if trailer.get("kind") != "entries":
        return LEGACY, trailer
    try:
        size_ok = os.path.getsize(path) == trailer["patched_size"]
        live = dead = True
        with open(path, "rb") as f:
            for e in trailer["entries"]:
                off, fid, sz = _read_slot(f, e["slot_off"])
                live = live and (off, sz) == tuple(e["new"]) and fid == e["orig"][1]
                dead = dead and (off, fid, sz) == tuple(e["orig"])
    except (KeyError, TypeError, OSError, struct.error):
        return STRANGE, trailer
    if size_ok and live:
        return PATCHED, trailer
    if size_ok and dead:
        return PARTIAL, trailer
    return STRANGE, trailer


def revert(path, state, trailer):
    """Bring a file we touched back to its original bytes. Safe to repeat after a crash."""
    if state == PATCHED:
        with open(path, "r+b") as f:
            for e in trailer["entries"]:
                set_slot(f, _Slot(e), *e["orig"][::2], e["orig_name_size"])
            f.flush()
            os.fsync(f.fileno())
            f.truncate(trailer["orig_size"])
            f.flush()
            os.fsync(f.fileno())
    elif state == PARTIAL:
        with open(path, "r+b") as f:
            f.truncate(trailer["orig_size"])
            f.flush()
            os.fsync(f.fileno())
    elif state == STRANGE:
        # The layout is no longer the one we recorded, so cutting to our size could damage
        # a file Steam has changed. Remove only our own marker.
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.truncate(size - ALIGN)


class _Slot:
    """What set_slot needs from an index entry, rebuilt from a trailer record."""

    def __init__(self, record):
        self.slot_off = record["slot_off"]
        self.name_off = record["name_off"]
        self.file_id = record["orig"][1]


def apply(path, profile, target, style, load_icon, info):
    """Patch one entry of the (original) file at `path`. Returns the new payload size."""
    fg = Forge(path)
    entry = fg.find(target["entry"])
    payload = fg.read_payload(entry)
    new_payload = profile.patch_payload(payload, target, style, load_icon)

    free = shutil.disk_usage(os.path.dirname(path)).free
    if free < len(new_payload) + MIN_FREE:
        raise RuntimeError("not enough free space (%d MB needed)" % ((len(new_payload) + MIN_FREE) >> 20))

    header = with_payload_size(read_entry_header(path, entry), len(new_payload))
    orig_size = os.path.getsize(path)
    with open(path, "r+b") as f:
        new_off = append_blob(f, header, new_payload)
        f.flush()
        os.fsync(f.fileno())
        blob_end = f.tell()
    record = dict(info)
    record.update(
        kind="entries",
        style=style,
        orig_size=orig_size,
        patched_size=blob_end + ALIGN,
        applied=int(time.time()),
        entries=[{
            "name": entry.name,
            "slot_off": entry.slot_off,
            "name_off": entry.name_off,
            "orig": [entry.offset, entry.file_id, entry.size],
            "orig_name_size": entry.name_size,
            "new": [new_off, len(new_payload)],
        }],
    )
    write_trailer(path, record)
    with open(path, "r+b") as f:
        set_slot(f, entry, new_off, len(new_payload), len(new_payload))
        f.flush()
        os.fsync(f.fileno())
    return len(new_payload)


def wanted(profile, style):
    """Targets that must carry glyphs for `style` (none for the original look)."""
    return [] if style == "original" else profile.targets_for(style)


def plan(profile, install_path, style):
    """What each target file needs, without touching anything.

    Returns [(target, path, state, trailer, ok)] where `ok` means "already as wanted".
    """
    want = {t["file"] for t in wanted(profile, style)}
    rows = []
    for target in profile.targets():
        path = os.path.join(install_path, target["file"])
        if not os.path.exists(path):
            rows.append((target, path, None, None, target["file"] not in want))
            continue
        state, trailer = inspect(path)
        if target["file"] in want:
            ok = (state == PATCHED and trailer.get("style") == style
                  and trailer.get("revision") == profile.revision)
        else:
            ok = state == ORIGINAL
        rows.append((target, path, state, trailer, ok))
    return rows


def applied_style(rows):
    """The style the files carry now: the one of the first live patched file, else original."""
    for _, _, state, trailer, _ in rows:
        if state == PATCHED:
            return trailer.get("style", "original")
    return "original"
