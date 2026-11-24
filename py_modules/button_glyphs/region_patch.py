"""Region-level patching: rewrite fixed-size byte ranges of a huge file in place.

State lives in a 44-byte marker inside each region's DDS header (reserved fields, zero in
the original), so the file never grows and Steam can still tell its chunks apart:

  magic(8) | state(1) | style(1) | revision(u16) | SHA-256 of the original region [:16]

  state "P"  region carries our glyphs (style, revision)
  state "W"  we were writing it; the region may be half-done and must be restored

Undoing is exact: the original bytes of every region are kept in a backup (a few MB, not
the whole archive) and written back. Write order for power loss: marker W -> body -> final
header, so a crash always leaves a "W" marker (or a pristine region) to recover from.

Steam "verify files" / updates restore whole chunks, and every chunk we touched differs
from the manifest, so they all come back as originals (no marker) and get patched again.
"""
import hashlib
import os
import shutil
import struct

MAGIC = b"BGLYPHS\x01"
MARK_OFF = 0x20
MARK_LEN = 44
HEADER = 128
STYLE_CODE = {"ps": 1, "xbox": 2}
CODE_STYLE = {v: k for k, v in STYLE_CODE.items()}
MIN_FREE = 64 << 20

ORIGINAL = "original"     # no marker
PATCHED = "patched"       # marker P
WRITING = "writing"       # marker W: interrupted write


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def make_marker(state, style, revision, source_sha):
    body = MAGIC + state + bytes((STYLE_CODE[style],)) + struct.pack("<H", revision) \
        + bytes.fromhex(source_sha)[:16]
    return body + bytes(MARK_LEN - len(body))


def parse_marker(header):
    """(state, style, revision, source16) from a region header, or None if it has no marker."""
    mark = header[MARK_OFF:MARK_OFF + MARK_LEN]
    if len(mark) < MARK_LEN or not mark.startswith(MAGIC):
        return None
    state = mark[8:9]
    if state not in (b"P", b"W"):
        return None
    style = CODE_STYLE.get(mark[9])
    if style is None:
        return None
    revision = struct.unpack_from("<H", mark, 10)[0]
    return state.decode(), style, revision, mark[12:28]


def read_header(f, region):
    f.seek(region["offset"])
    return f.read(HEADER)


def read_region(f, region):
    f.seek(region["offset"])
    return f.read(region["size"])


def region_state(f, region):
    """(state, marker) from the header alone - cheap enough for the periodic check."""
    marker = parse_marker(read_header(f, region))
    if marker is None:
        return ORIGINAL, None
    return (PATCHED if marker[0] == "P" else WRITING), marker


def plan(profile, install_path, style):
    """[(region, state, marker, ok)] for every region; ok = already as wanted.

    `style` is "original" or a style the profile can draw.
    """
    path = os.path.join(install_path, profile.file)
    rows = []
    with open(path, "rb") as f:
        for region in profile.regions:
            state, marker = region_state(f, region)
            if style == "original":
                ok = state == ORIGINAL
            else:
                ok = (state == PATCHED and marker[1] == style
                      and marker[2] == profile.revision)
            rows.append((region, state, marker, ok))
    return rows


def applied_style(rows):
    for _, state, marker, _ in rows:
        if state == PATCHED:
            return marker[1]
    return "original"


def backup_file(backup_dir, region):
    return os.path.join(backup_dir, region["key"].replace("/", "_") + ".orig")


def _write(f, offset, data):
    f.seek(offset)
    f.write(data)
    f.flush()
    os.fsync(f.fileno())


def check(profile, install_path, backup_dir, rows):
    """Problems that make the regions in `rows` unsafe to write (empty list = OK)."""
    problems = []
    path = os.path.join(install_path, profile.file)
    with open(path, "rb") as f:
        for region, state, marker, _ in rows:
            bak = backup_file(backup_dir, region)
            if state == ORIGINAL:
                if sha256(read_region(f, region)) != region["sha256"]:
                    problems.append("%s: texture differs from the supported build" % region["key"])
            elif not os.path.exists(bak):
                problems.append("%s: backup of the original is missing" % region["key"])
    return problems


def _original_bytes(f, region, state, backup_dir):
    """Verified original bytes of a region (from the file when pristine, else the backup)."""
    if state == ORIGINAL:
        data = read_region(f, region)
    else:
        with open(backup_file(backup_dir, region), "rb") as b:
            data = b.read()
    if sha256(data) != region["sha256"]:
        raise RuntimeError("%s: original bytes do not match (use Steam > Verify files)"
                           % region["key"])
    return data


def ensure_backups(profile, install_path, backup_dir, rows):
    """Save the original bytes of every pristine region (once; verified by SHA-256)."""
    path = os.path.join(install_path, profile.file)
    os.makedirs(backup_dir, exist_ok=True)
    need = sum(r[0]["size"] for r in rows if r[1] == ORIGINAL
               and not os.path.exists(backup_file(backup_dir, r[0])))
    if need and shutil.disk_usage(backup_dir).free < need + MIN_FREE:
        raise RuntimeError("not enough free space (%d MB needed)" % ((need + MIN_FREE) >> 20))
    with open(path, "rb") as f:
        for region, state, _, _ in rows:
            bak = backup_file(backup_dir, region)
            if state != ORIGINAL:
                continue
            if os.path.exists(bak):
                with open(bak, "rb") as b:
                    if sha256(b.read()) == region["sha256"]:
                        continue
            data = read_region(f, region)
            if sha256(data) != region["sha256"]:
                raise RuntimeError("%s: texture differs from the supported build" % region["key"])
            tmp = bak + ".tmp"
            with open(tmp, "wb") as b:
                b.write(data)
                b.flush()
                os.fsync(b.fileno())
            os.replace(tmp, bak)


def _put(f, region, new, marker_state_first):
    """Write a whole region: marker W -> body -> header. `new` is the final region bytes."""
    off = region["offset"]
    if marker_state_first is not None:
        _write(f, off + MARK_OFF, marker_state_first)
    _write(f, off + HEADER, new[HEADER:])
    _write(f, off, new[:HEADER])


def apply(profile, install_path, backup_dir, rows, style):
    """Draw `style` into every region (rows from plan(); backups must exist)."""
    path = os.path.join(install_path, profile.file)
    with open(path, "r+b") as f:
        for region, state, _, ok in rows:
            if ok:
                continue
            original = _original_bytes(f, region, state, backup_dir)
            new = bytearray(profile.render(original, region, style))
            if len(new) != region["size"]:         # a region never changes size; refuse before writing
                raise RuntimeError("%s: rendered %d bytes, expected %d"
                                   % (region["key"], len(new), region["size"]))
            src = region["sha256"]
            writing = make_marker(b"W", style, profile.revision, src)
            new[MARK_OFF:MARK_OFF + MARK_LEN] = make_marker(b"P", style, profile.revision, src)
            _put(f, region, bytes(new), writing)


def revert(profile, install_path, backup_dir, rows):
    """Write the original bytes back into every region that is not pristine."""
    path = os.path.join(install_path, profile.file)
    with open(path, "r+b") as f:
        for region, state, marker, _ in rows:
            if state == ORIGINAL:
                continue
            original = _original_bytes(f, region, state, backup_dir)
            style = marker[1] if marker else "ps"
            _put(f, region, original, make_marker(b"W", style, profile.revision, region["sha256"]))
