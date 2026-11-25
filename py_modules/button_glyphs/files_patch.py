"""Files-level patching: small loose files rewritten whole, one by one, with no marker in them.

For games whose art is a set of tiny loose files (Songs for a Hero: 24 XNB textures of 761-2661
bytes). A file such as an XNB ends exactly at its last pixel, so there is no spare byte for a
marker (region_patch.py hides one in a DDS header; an XNB has no such place). The state is
therefore read from SHA-256, per file:

  original  the bytes equal the pinned original (fresh install, Steam "verify files", an update
            that restored the file)
  patched   the bytes equal what the profile draws now from the backed-up original
  stale     an earlier output of ours: a verified backup exists, the first `header` bytes are the
            original's, and the file's SHA-256 is in the render ledger (below) but it is not what is
            drawn now (the art or the profile changed after the install). It is rebuilt from the
            BACKUP, never from the stale file, so nothing stacks
  orphan    ours (equal to today's render, or its SHA-256 is in the ledger) but there is no verified
            backup to undo it from (the plugin's data was lost): an error, never touched
  unknown   anything else (another build of the game, a game update that changed only the pixels,
            an edited or truncated file, a drawing whose ledger was lost)
  missing   the file is not there

Ownership ledger. Without a marker, "same size, same header, different pixels" cannot tell an older
drawing of ours from a texture that a game update changed. So `renders.log` (in the backup folder)
records the SHA-256 of every render BEFORE the first file of an apply is written: an atomically
rewritten text file, one `<sha256> <profile revision>` line per render, de-duplicated and capped
(MAX_LEDGER, the oldest are dropped); a torn or damaged line is ignored. Only a file whose
SHA-256 is in the ledger can be stale. If the ledger is lost, older drawings become unknown: that is
the safe side (nothing is rewritten; a style is refused with the "Verify files" hint).

"unknown" is a refusal for a style (nothing at all is written, the error is shown). Steam > Verify
files helps only when the cause was art we left behind: it turns the file back into the pinned original,
which is applied again. After a game update Verify brings back the NEW build, which is still not the
pinned one. Choosing Original leaves unknown and missing files alone (they are not ours) and the status
says so (`differs()`: an error when the plugin has handled the game before, a plain notice when it never has).

The profile supplies `files` ([{key, file, size, sha256, header, ...}], `file` relative to the
install dir), `patch_styles`, `revision` and `render(original, entry, style) -> bytes` of the same
size and the same first `header` bytes. A profile never opens a file or a backup.

Safety, in the order things happen:
  1. plan + check: every file is read (at most size+1 bytes) and classified; a missing or unknown
     file stops the run before anything is written
  2. ensure_backups: the full original file of every pristine file is written to the plugin's
     backup folder (temp + fsync + rename), then read back and compared with the pinned SHA-256
  3. apply: every new file is rendered from a verified backup first (a bad picture stops here,
     with nothing written); the render hashes are recorded in the ledger; then each file is
     replaced atomically (temp file in the same folder, fsync, rename, fsync of the folder) and
     read back
  Undo (revert) writes the verified backups back the same way, in reverse order, and checks that
  every restored file equals the pinned SHA-256.
Whatever the moment a crash happens, each file is either one of its two complete versions, so
the next sync finishes the job or Original restores it. Backups are never deleted.
"""
import collections
import hashlib
import os
import re
import shutil
import stat

TMP_SUFFIX = ".button-glyphs.tmp"
MIN_FREE = 64 << 20
MAX_FILE = 4 << 20            # files here are tiny; a profile that lists a bigger one is a mistake
LEDGER = "renders.log"
MAX_LEDGER = 4096             # lines kept (~300 KB); the oldest are dropped first
MAX_LEDGER_BYTES = 1 << 20    # never read more than this
LEDGER_LINE = re.compile(rb"([0-9a-f]{64}) ([0-9]{1,6})")
HINT_OURS = " (Steam > Verify files restores the original files)"
HINT_FOREIGN = (" (a game update, or art left by a lost plugin install; "
                "Steam > Verify files helps only in the second case)")

ORIGINAL = "original"
PATCHED = "patched"
STALE = "stale"
ORPHAN = "orphan"
UNKNOWN = "unknown"
MISSING = "missing"

Row = collections.namedtuple("Row", "entry path state style")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def fsync_dir(path):
    """Flush a folder's entries (a rename) to disk; best effort where the OS does not allow it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def file_path(install_path, entry):
    return os.path.join(install_path, *entry["file"].split("/"))


def backup_file(backup_dir, entry):
    return os.path.join(backup_dir, entry["key"].replace("/", "_") + ".orig")


def read_file(path, size):
    """At most size+1 bytes of `path` (a file that grew shows as longer than `size`, never as a flood)."""
    with open(path, "rb") as f:
        return f.read(size + 1)


def write_atomic(path, data, prepare=None):
    """Create or replace `path` with `data` so that a crash leaves the old or the new file, whole.

    The temp file lives in the same folder (a rename stays on one filesystem) and is removed on any
    error; the folder is flushed afterwards so the rename survives a power cut. `prepare(tmp)` (optional, the overlay
    strategy's ownership fix when running as root) runs on the temp file AFTER its data is flushed and BEFORE the rename,
    so no crash window leaves a final file that was never prepared.
    """
    tmp = path + TMP_SUFFIX
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))     # keep the permissions of the file
        except OSError:
            pass
        if prepare is not None:
            prepare(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    fsync_dir(os.path.dirname(path))


def clean_temps(profile, install_path, backup_dir):
    """Remove temp files a crashed run left behind (only names this module creates, the ledger's too)."""
    stale = [ledger_path(backup_dir) + TMP_SUFFIX]
    for entry in profile.files:
        stale.append(file_path(install_path, entry) + TMP_SUFFIX)
        stale.append(backup_file(backup_dir, entry) + TMP_SUFFIX)
    for tmp in stale:
        try:
            os.remove(tmp)
        except OSError:
            pass


def ledger_path(backup_dir):
    return os.path.join(backup_dir, LEDGER)


def read_ledger(backup_dir):
    """{sha256: revision} of the valid ledger lines (oldest first). Damage costs recognition, never safety.

    Only lines that end with a newline count, so a torn last line is ignored; so is any line that is
    not exactly `<64 hex> <number>`; at most MAX_LEDGER_BYTES are read.
    """
    try:
        with open(ledger_path(backup_dir), "rb") as f:
            raw = f.read(MAX_LEDGER_BYTES)
    except OSError:
        return {}
    known = {}
    for line in raw.split(b"\n")[:-1]:          # the piece after the last newline is torn or empty
        m = LEDGER_LINE.fullmatch(line)
        if m:
            digest = m.group(1).decode("ascii")
            known.pop(digest, None)                # a repeated hash moves to the end: no duplicates
            known[digest] = int(m.group(2))
    return known


def record_renders(backup_dir, hashes, revision):
    """Durably add the SHA-256 of renders about to be written (atomic rewrite, de-duplicated, capped).

    Called BEFORE the first file of an apply is written: a crash in between leaves hashes of files
    that were never written, which only means "a file with exactly these bytes is ours".
    """
    os.makedirs(backup_dir, exist_ok=True)
    known = read_ledger(backup_dir)
    for digest in hashes:
        known.pop(digest, None)
        known[digest] = revision
    lines = ["%s %d\n" % item for item in list(known.items())[-MAX_LEDGER:]]
    write_atomic(ledger_path(backup_dir), "".join(lines).encode("ascii"))


def backup_data(backup_dir, entry):
    """The verified original bytes of an entry from its backup, or None (missing or not matching)."""
    path = backup_file(backup_dir, entry)
    try:
        if os.path.getsize(path) != entry["size"]:
            return None
        data = read_file(path, entry["size"])
    except OSError:
        return None
    return data if len(data) == entry["size"] and sha256(data) == entry["sha256"] else None


def _drawn_style(profile, entry, source, target):
    """The style whose render of `source` equals `target`, or None (a render that fails is "no")."""
    for style in profile.patch_styles:
        try:
            if profile.render(source, entry, style) == target:
                return style
        except Exception:
            continue
    return None


def classify(profile, entry, path, backup_dir, known):
    """(state, style) of one file; `known` = the ledger's hashes. style is set for patched/orphan."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return MISSING, None
    if not stat.S_ISREG(info.st_mode) or info.st_size != entry["size"]:
        return UNKNOWN, None
    data = read_file(path, entry["size"])
    if len(data) != entry["size"]:                  # it changed under us
        return UNKNOWN, None
    digest = sha256(data)
    if digest == entry["sha256"]:
        return ORIGINAL, None
    backup = backup_data(backup_dir, entry)
    if backup is None:
        style = _drawn_style(profile, entry, data, data)
        return (ORPHAN, style) if style or digest in known else (UNKNOWN, None)
    header = entry["header"]
    if data[:header] != backup[:header]:
        return UNKNOWN, None
    style = _drawn_style(profile, entry, backup, data)
    if style:
        return PATCHED, style
    return (STALE, None) if digest in known else (UNKNOWN, None)


def plan(profile, install_path, backup_dir):
    """A Row for every file of the profile, in profile order."""
    known = read_ledger(backup_dir)
    rows = []
    for entry in profile.files:
        path = file_path(install_path, entry)
        state, style = classify(profile, entry, path, backup_dir, known)
        rows.append(Row(entry, path, state, style))
    return rows


def is_ok(row, want):
    """True when the file needs no work for `want` ("original" or a style)."""
    if want == "original":
        return row.state in (ORIGINAL, UNKNOWN, MISSING)      # unknown/missing are not ours: left alone
    return row.state == PATCHED and row.style == want


def applied_style(rows, native, default):
    """The style the files show: a patched file's, `default` for stale/orphan ones, else `native`."""
    for row in rows:
        if row.state == PATCHED:
            return row.style
    for row in rows:
        if row.state in (STALE, ORPHAN):
            return row.style or default
    return native


def check(rows, want):
    """A message that says why `want` cannot be reached safely, or "" when it can."""
    problems = []
    foreign = False                            # some file is not ours: a game update is possible
    for row in rows:
        key = row.entry["key"]
        if row.state == ORPHAN:
            problems.append("%s: the backup of the original is missing" % key)
        elif want != "original" and row.state == MISSING:
            problems.append("%s: file not found" % key)
            foreign = True
        elif want != "original" and row.state == UNKNOWN:
            problems.append("%s: differs from the supported build" % key)
            foreign = True
    if not problems:
        return ""
    head = "cannot restore: " if want == "original" else "this game version is not supported yet: "
    return head + "; ".join(problems[:3]) + (HINT_FOREIGN if foreign else HINT_OURS)


def differs(rows, touched=True):
    """A message when some files are not the supported build (Original leaves them alone but says so).

    `touched` = this plugin has handled the game before (backups or a recorded apply): the files may be
    art we left behind. Otherwise they can only be another build and the message is a plain notice.
    """
    n = sum(1 for r in rows if r.state in (UNKNOWN, MISSING))
    if not n:
        return ""
    if not touched:
        return ("%d of %d files are not the supported build (the game was probably updated); "
                "PlayStation cannot be applied" % (n, len(rows)))
    return ("%d of %d files are not the supported build (a game update, or art left by a lost plugin install); "
            "Steam > Verify files helps only in the second case" % (n, len(rows)))


def ensure_backups(profile, install_path, backup_dir, rows):
    """Save the full original file of every pristine file that has no verified backup yet.

    Each backup is written atomically, read back and compared with the pinned SHA-256. An existing
    backup that does not match is replaced from the (verified) pristine file; one that matches is
    never rewritten.
    """
    need = [r for r in rows if r.state == ORIGINAL and backup_data(backup_dir, r.entry) is None]
    if not need:
        return
    os.makedirs(backup_dir, exist_ok=True)
    total = sum(r.entry["size"] for r in need)
    if shutil.disk_usage(backup_dir).free < total + MIN_FREE:
        raise RuntimeError("not enough free space (%d MB needed)" % ((total + MIN_FREE) >> 20))
    for row in need:
        data = read_file(row.path, row.entry["size"])
        if sha256(data) != row.entry["sha256"]:
            raise RuntimeError("%s: differs from the supported build" % row.entry["key"])
        bak = backup_file(backup_dir, row.entry)
        write_atomic(bak, data)
        if sha256(read_file(bak, row.entry["size"])) != row.entry["sha256"]:
            raise RuntimeError("%s: the backup could not be verified" % row.entry["key"])


def _need_room(install_path, rows):
    total = sum(r.entry["size"] for r in rows)
    if shutil.disk_usage(install_path).free < total + MIN_FREE:
        raise RuntimeError("not enough free space in the game folder (%d MB needed)"
                           % ((total + MIN_FREE) >> 20))


def apply(profile, install_path, backup_dir, rows, style):
    """Draw `style` into every file that is not there yet (rows from plan(); backups must exist)."""
    todo = [r for r in rows if not is_ok(r, style)]
    jobs = []
    for row in todo:                      # render everything first: a bad picture writes nothing
        original = backup_data(backup_dir, row.entry)
        if original is None:
            raise RuntimeError("%s: the backup of the original is missing" % row.entry["key"])
        new = profile.render(original, row.entry, style)
        if len(new) != row.entry["size"]:
            raise RuntimeError("%s: rendered %d bytes, expected %d"
                               % (row.entry["key"], len(new), row.entry["size"]))
        if new[:row.entry["header"]] != original[:row.entry["header"]]:
            raise RuntimeError("%s: the render changed the file header" % row.entry["key"])
        jobs.append((row, new))
    _need_room(install_path, todo)
    if jobs:                              # ownership first: these hashes are on disk before any file changes
        record_renders(backup_dir, [sha256(new) for _, new in jobs], profile.revision)
    for row, new in jobs:
        write_atomic(row.path, new)
        if sha256(read_file(row.path, len(new))) != sha256(new):
            raise RuntimeError("%s: the file does not hold the new data after writing" % row.entry["key"])


def revert(profile, install_path, backup_dir, rows):
    """Write the verified original back into every file that holds our output (reverse order)."""
    todo = [r for r in rows if r.state in (PATCHED, STALE)]
    jobs = []
    for row in reversed(todo):
        original = backup_data(backup_dir, row.entry)
        if original is None:
            raise RuntimeError("%s: the backup of the original is missing" % row.entry["key"])
        jobs.append((row, original))
    _need_room(install_path, todo)
    for row, original in jobs:
        write_atomic(row.path, original)
        if sha256(read_file(row.path, row.entry["size"])) != row.entry["sha256"]:
            raise RuntimeError("%s: the restored file does not match the original" % row.entry["key"])
