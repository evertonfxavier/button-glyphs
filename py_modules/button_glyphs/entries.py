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

Write order matters for power loss (see apply() and revert() for the why): trailer ->
blob -> slot -> name size, and the reverse to undo: slot -> name size -> truncate. Every
combination of the two index fields (slot, name-record size) is a recognised state, so at
every instant the file is either fully original, or recoverable from the trailer.
"""
import os
import shutil
import struct
import time

from .forge import ALIGN, Forge, ForgeError, read_entry_header, with_payload_size
from .trailer import read_trailer, write_trailer

SLOT = struct.Struct("<qII")
MIN_FREE = 64 << 20

ORIGINAL = "original"    # no trailer: untouched (or restored) file
PATCHED = "patched"      # trailer matches the index: our change is live
PARTIAL = "partial"      # we crashed before repointing the index: blob is dead weight
HALF = "half"            # a power cut between the two index fields (slot / name size): every field is
                         # one of OUR recorded values (old or new) but they do not agree
STRANGE = "strange"      # trailer present but the file no longer matches it (game update?)
LEGACY = "legacy"        # trailer of the old whole-file strategy


def _read_slot(f, off):
    f.seek(off)
    return SLOT.unpack(f.read(SLOT.size))


def _read_name_size(f, off):
    f.seek(off)
    return struct.unpack("<I", f.read(4))[0]


def inspect(path):
    """Classify `path`. Returns (state, trailer-or-None).

    An entry has two index fields we move: the slot (offset, file_id, size) and the size kept in
    its name record. Each must equal one of the values the trailer recorded (original or new);
    anything else, in any field, is STRANGE (a game update, never repaired by guesswork). All
    entries new -> PATCHED, all original -> PARTIAL, any other mix of OUR values -> HALF.
    """
    trailer = read_trailer(path)
    if trailer is None:
        return ORIGINAL, None
    if trailer.get("kind") != "entries":
        return LEGACY, trailer
    try:
        size_ok = os.path.getsize(path) == trailer["patched_size"]
        all_new = all_orig = known = True
        with open(path, "rb") as f:
            for e in trailer["entries"]:
                off, fid, sz = _read_slot(f, e["slot_off"])
                name = _read_name_size(f, e["name_off"])
                slot_new = (off, sz) == tuple(e["new"]) and fid == e["orig"][1]
                slot_orig = (off, fid, sz) == tuple(e["orig"])
                name_new = name == e["new"][1]
                name_orig = name == e["orig_name_size"]
                all_new = all_new and slot_new and name_new
                all_orig = all_orig and slot_orig and name_orig
                known = known and (slot_new or slot_orig) and (name_new or name_orig)
    except (KeyError, TypeError, OSError, struct.error):
        return STRANGE, trailer
    if not size_ok:
        return STRANGE, trailer
    if all_new:
        return PATCHED, trailer
    if all_orig:
        return PARTIAL, trailer
    if known:
        return HALF, trailer
    return STRANGE, trailer


def _write_field(f, off, data):
    """One index field, durable before the next one is touched."""
    f.seek(off)
    f.write(data)
    f.flush()
    os.fsync(f.fileno())


def _set_slot(f, e, offset, size):
    _write_field(f, e["slot_off"], SLOT.pack(offset, e["orig"][1], size))


def _set_name_size(f, e, size):
    _write_field(f, e["name_off"], struct.pack("<I", size))


def revert(path, state, trailer):
    """Bring a file we touched back to its original bytes. Safe to repeat after a crash.

    Total for every state that carries our trailer at the size we recorded (PATCHED, HALF, PARTIAL):
    each index field that does not hold its original value is written back from the trailer, the slot
    first and durable before the name size, then the file is cut. A power cut anywhere in here leaves
    one of those same three states, so running it again converges; PARTIAL used to skip the fields,
    which left a name size we had already moved (the cut between the two writes of an old Original).
    """
    if state in (PATCHED, HALF, PARTIAL):
        with open(path, "r+b") as f:
            for e in trailer["entries"]:
                if _read_slot(f, e["slot_off"]) != tuple(e["orig"]):
                    _set_slot(f, e, e["orig"][0], e["orig"][2])
                if _read_name_size(f, e["name_off"]) != e["orig_name_size"]:
                    _set_name_size(f, e, e["orig_name_size"])
            f.truncate(trailer["orig_size"])
            f.flush()
            os.fsync(f.fileno())
    elif state == STRANGE:
        # The layout is no longer the one we recorded, so cutting to our size could damage
        # a file Steam has changed. Remove only our own marker.
        size = os.path.getsize(path)
        with open(path, "r+b") as f:
            f.truncate(size - ALIGN)


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
    new_off = (orig_size + ALIGN - 1) // ALIGN * ALIGN
    blob_end = (new_off + len(header) + len(new_payload) + ALIGN - 1) // ALIGN * ALIGN
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
    # Why this order: the trailer goes FIRST, at its final place, in ONE write that also extends the
    # file to patched_size (the gap reads as zeros, exactly what the old pad bytes were: the finished
    # file is byte-identical to the old blob -> trailer order). Before, a power cut while the blob was
    # being written left a longer file with no trailer, which reads as a clean original of the wrong
    # size, so a later undo kept the junk. Now every cut before the slot is PARTIAL: the trailer knows
    # orig_size and the slot still points at the untouched original.
    write_trailer(path, record, at=blob_end)
    with open(path, "r+b") as f:
        f.seek(new_off)
        f.write(header)
        f.write(new_payload)
        f.flush()
        os.fsync(f.fileno())
    # Then the two index fields, each durable before the next. They are not adjacent (the index and
    # the name table are far apart), so no single write covers both; instead every combination of
    # old/new values is a state inspect() recognises (HALF) and a sync repairs, so the order does not
    # matter for safety: slot first means a cut leaves the new entry live behind an old name size.
    rec = record["entries"][0]
    with open(path, "r+b") as f:
        _set_slot(f, rec, new_off, len(new_payload))
        _set_name_size(f, rec, len(new_payload))
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
