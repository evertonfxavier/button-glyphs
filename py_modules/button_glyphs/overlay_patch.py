"""Add-file overlay: a style is an extra file the game loads AFTER its own files, so it wins. Nothing of the game is touched.

For engines that mount every archive of the game folder in sorted order and let a LATER archive's entry replace an
earlier one of the same name (Croteam's Serious Engine: Serious Sam 2 and Serious Sam HD load every root `.gro`
alphabetically). The plugin does not edit a single game byte: it ADDS one small archive (`zz_ButtonGlyphs.gro`,
the *gro* vehicle) or, where the engine ignores an unsigned archive, the same entries as loose files under the game
folder (the *loose* vehicle). Each replacement entry is built at apply time from the ORIGINAL entry the game itself
ships (read from its archives, never from a copy of ours), so it always matches the installed build.

  exact undo     = delete what we added (and the empty folders we created); the game files were never opened for writing
  never stacks   = every output is built from the pinned original entries; our own archive is excluded from the read
  state          = read from the files: our file absent (original), present and equal to what we would build now
                   (patched), present, ours but different (stale: an older drawing, rebuilt), present and not ours
                   (unknown: refused for a style, left alone by Original and reported). There is no marker to
                   write, so "ours" is the SHA-256 ledger of everything we write (files_patch.renders.log, fsynced
                   BEFORE the first write) or byte-equality with what we would build today.
  Steam verify   = may delete our extra file: that is simply "original" again (the status says it will be re-applied).
                   A game update that changes an original entry is an unknown build: refused, nothing written.

The profile supplies `entries` ([{key, path, size, sha256, gro, ...}]: the originals it replaces, pinned by SHA-256, the
archive that wins them), `mount` (folder of the archives, relative to the install), `archive` (our file name),
`vehicle` ("gro" | "loose"), `patch_styles`, `revision`, `preflight()` and `outputs(style, originals) -> {path: bytes}`
(already proven to decode back to the intended picture). The core never opens a picture.

Order of events (a power cut at any of them leaves a recognised state):
  1. plan: read every original entry (bounded, SHA-256 against the pin) -> unknown build refuses, nothing written
  2. build every output in memory, prove the container reads back (zip CRC, names, bytes)
  3. room check, then ledger (atomic, fsynced) with the SHA-256 of every file about to be written
  4. loose vehicle only: the folders to create are journaled (dirs.log), then made
  5. each file replaced atomically (temp + fsync + os.replace + fsync of the folder), read back
  6. files of the OTHER vehicle that are ours (vehicle switch) removed last
  Undo removes our files in reverse order (each removal re-checks the hash it is about to delete), then the journaled
  folders that are empty. A cut leaves a prefix of the loose files: every prefix is "half" = some patched, some
  missing, finished by the next sync or undone by Original.
"""
import collections
import io
import os
import re
import shutil
import stat
import zipfile

from . import files_patch, gro

TMP_SUFFIX = files_patch.TMP_SUFFIX
MIN_FREE = files_patch.MIN_FREE
MAX_TARGET = 32 << 20            # a file of ours is never bigger (the HD overlay holds a 12 MB atlas); a bigger one with our name is "unknown", not read
JOURNAL = "dirs.log"             # folders this plugin created for the loose vehicle (bounded, atomically rewritten)
MAX_JOURNAL = 256
MAX_JOURNAL_BYTES = 64 << 10
JOURNAL_LINE = re.compile(r"[A-Za-z0-9 ._()\-]+(?:/[A-Za-z0-9 ._()\-]+)*")

VEHICLES = ("gro", "loose")

MISSING = "missing"
PATCHED = "patched"
STALE = "stale"
UNKNOWN = "unknown"

Row = collections.namedtuple("Row", "rel path state style active sha")
Problem = collections.namedtuple("Problem", "code message args")


class Refusal(Exception):
    """The game folder is not one this profile knows (an unknown build): nothing may be written."""

    def __init__(self, message, code="unsupported_build", args=None):
        Exception.__init__(self, message)
        self.problem = Problem(code, message, args if args is not None else {"detail": message})


def sha256(data):
    return files_patch.sha256(data)


# ------------------------------------------------------------------ names and paths


def _join(*parts):
    return "/".join(p for p in parts if p and p != ".")


def archive_rel(profile):
    return _join(profile.mount, profile.archive)


def loose_rel(profile, entry):
    return _join(getattr(profile, "loose_root", profile.mount), entry["path"])


def active_rels(profile, vehicle=None):
    """The install-relative names of what the vehicle writes, in write order."""
    vehicle = vehicle or profile.vehicle
    if vehicle == "gro":
        return [archive_rel(profile)]
    return [loose_rel(profile, e) for e in profile.entries]


def other_rels(profile):
    return active_rels(profile, "loose" if profile.vehicle == "gro" else "gro")


def abspath(install, rel):
    return os.path.join(install, *rel.split("/"))


def targets(profile, outputs):
    """[(rel, bytes)] the active vehicle writes for `outputs` ({entry path: bytes}); the gro is proven to read back."""
    if profile.vehicle == "gro":
        order = [(e["path"], outputs[e["path"]]) for e in profile.entries]
        data = gro.build(order)
        _prove_gro(data, order)
        return [(archive_rel(profile), data)]
    return [(loose_rel(profile, e), outputs[e["path"]]) for e in profile.entries]


def _prove_gro(data, order):
    """The archive we are about to write opens as a zip, passes its CRCs and holds exactly the intended bytes."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if z.testzip() is not None:
            raise RuntimeError("the archive built in memory fails its own CRC check")
        if [i.filename for i in z.infolist()] != [n for n, _ in order]:
            raise RuntimeError("the archive built in memory does not list the intended entries")
        for name, blob in order:
            if z.read(name) != blob:
                raise RuntimeError("%s: the archive built in memory does not hold the intended bytes" % name)


# ------------------------------------------------------------------ reading the game's own entries


def archives(profile, install):
    """The game's archives in LOAD order (sorted by name, case-insensitive), without our own."""
    folder = abspath(install, profile.mount) if profile.mount not in ("", ".") else install
    ours = profile.archive.lower()
    names = [n for n in os.listdir(folder) if n.lower().endswith(".gro") and n.lower() != ours]
    names.sort(key=lambda n: (n.lower(), n))
    return [os.path.join(folder, n) for n in names if os.path.isfile(os.path.join(folder, n))]


def read_originals(profile, install, paths=None):
    """{entry path: original bytes}, each checked against its pin. Raises Refusal on ANY difference.

    Only entries the profile names are read (at most `size` bytes each); the archives themselves are never read whole.
    `paths` (load order) replaces the folder listing: tools that hold copies of the archives outside an install use it.
    """
    paths = archives(profile, install) if paths is None else list(paths)
    if not paths:
        raise Refusal("no .gro archive found in the game folder")
    bad, out = [], {}
    try:
        with gro.Layers(paths) as layers:
            for e in profile.entries:
                try:
                    hit = layers.read(e["path"], max_bytes=e["size"])
                except gro.GroError as exc:
                    bad.append("%s: %s" % (e["key"], exc))
                    continue
                if hit is None:
                    bad.append("%s: not found in any archive" % e["key"])
                    continue
                _, data, winner = hit
                if len(data) != e["size"] or sha256(data) != e["sha256"]:
                    bad.append("%s: differs from the supported build" % e["key"])
                elif e.get("gro") and os.path.basename(winner) != e["gro"]:
                    bad.append("%s: comes from %s, not %s" % (e["key"], os.path.basename(winner), e["gro"]))
                else:
                    out[e["path"]] = data
    except gro.GroError as exc:
        raise Refusal(str(exc), "cannot_read", {"file": profile.mount, "reason": str(exc)})
    if bad:
        raise Refusal("%d of %d entries are not the supported build (%s)%s"
                      % (len(bad), len(profile.entries), "; ".join(bad[:3]), files_patch.HINT_FOREIGN))
    return out


# ------------------------------------------------------------------ cheap facts about the game's archives

_DIRS = {}                       # realpath -> (stat key, {lower name: (archive basename, size, crc32)}) : parsed once


def clear_caches():
    """Forget the parsed archive directories (tests that rewrite an archive in place call this)."""
    _DIRS.clear()


def _stat_key(path):
    info = os.stat(path)
    return (info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino)


def _directory(path):
    """{lower entry name: (size, crc32)} of one archive's CENTRAL DIRECTORY (no entry is read or inflated).

    Parsed once per archive and kept while its (size, mtime, ctime, inode) stay the same: a status poll every minute
    then costs a few stat() calls. Any change to the file (a game update, Steam verify) parses it again."""
    real = os.path.realpath(path)
    key = _stat_key(real)
    hit = _DIRS.get(real)
    if hit and hit[0] == key:
        return hit[1]
    try:
        with zipfile.ZipFile(real) as z:
            names = {i.filename.replace("\\", "/").lower(): (i.file_size, i.CRC) for i in z.infolist()}
    except (zipfile.BadZipFile, OSError) as exc:
        raise Refusal("%s: cannot be read (%s)" % (os.path.basename(path), exc), "cannot_read",
                      {"file": os.path.basename(path), "reason": str(exc)})
    _DIRS[real] = (key, names)
    return names


def facts(profile, install):
    """Cheap proof that the game's entries are the supported build: central-directory size + CRC-32 + the winning
    archive's name for every pinned entry. Raises Refusal on any difference. Reads no entry data.

    The full SHA-256 check (`read_originals`) stays in apply; this answers the status poll and the idle watcher tick.
    """
    paths = archives(profile, install)
    if not paths:
        raise Refusal("no .gro archive found in the game folder")
    index = {}
    for path in paths:                                   # load order: the last archive that has a name wins
        base = os.path.basename(path)
        for name, (size, crc) in _directory(path).items():
            index[name] = (base, size, crc)
    bad = []
    for e in profile.entries:
        hit = index.get(e["path"].lower())
        if hit is None:
            bad.append("%s: not found in any archive" % e["key"])
        elif e.get("gro") and hit[0] != e["gro"]:
            bad.append("%s: comes from %s, not %s" % (e["key"], hit[0], e["gro"]))
        elif hit[1] != e["size"] or (e.get("crc32") and hit[2] != int(e["crc32"], 16)):
            bad.append("%s: differs from the supported build" % e["key"])
    if bad:
        raise Refusal("%d of %d entries are not the supported build (%s)%s"
                      % (len(bad), len(profile.entries), "; ".join(bad[:3]), files_patch.HINT_FOREIGN))


# ------------------------------------------------------------------ what we built, by fingerprint

BUILDS = "builds.log"            # `<fingerprint> <style> <sha256 of a file we wrote>`: which build a file belongs to
MAX_BUILDS = 512
MAX_BUILDS_BYTES = 128 << 10
BUILD_LINE = re.compile(rb"([0-9a-f]{64}) ([a-z0-9_]{1,16}) ([0-9a-f]{64})")


def _builds_path(backup_dir):
    return os.path.join(backup_dir, BUILDS)


def read_builds(backup_dir):
    """{(fingerprint, file sha256): style}. Damage costs recognition (the file turns stale and is rebuilt), never safety."""
    try:
        with open(_builds_path(backup_dir), "rb") as f:
            raw = f.read(MAX_BUILDS_BYTES)
    except OSError:
        return {}
    out = {}
    for line in raw.split(b"\n")[:-1]:
        m = BUILD_LINE.fullmatch(line)
        if m:
            key = (m.group(1).decode(), m.group(3).decode())
            out.pop(key, None)
            out[key] = m.group(2).decode()
    return out


def record_builds(backup_dir, items):
    """Durably add [(fingerprint, style, sha256)] (atomic rewrite, de-duplicated, capped); before the first write."""
    os.makedirs(backup_dir, exist_ok=True)
    known = read_builds(backup_dir)
    for fp, style, digest in items:
        known.pop((fp, digest), None)
        known[(fp, digest)] = style
    lines = ["%s %s %s\n" % (fp, style, digest) for (fp, digest), style in list(known.items())[-MAX_BUILDS:]]
    files_patch.write_atomic(_builds_path(backup_dir), "".join(lines).encode("ascii"))


def _fingerprints(profile, styles):
    """{style: fingerprint or None} (None when the bundled art cannot be read: nothing can be proven current)."""
    out = {}
    for style in styles:
        try:
            out[style] = profile.fingerprint(style)
        except Exception:
            out[style] = None
    return out


# ------------------------------------------------------------------ state


def _stat_sha(path):
    """(state kind, sha) of what sits at `path`: ("missing", None) | ("odd", None) | ("file", sha256)."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "missing", None
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_TARGET:
        return "odd", None
    data = files_patch.read_file(path, MAX_TARGET)
    if len(data) > MAX_TARGET:
        return "odd", None
    return "file", sha256(data)


def _classify(path, known, builds, fps, wants=None):
    """(state, style, sha) of one path.

    patched = its SHA-256 is recorded for the current fingerprint of a style (cheap: nothing is rebuilt), or equals
    `wants[style]` (the bytes just built); stale = ours (ledger) but not current; unknown = anything else."""
    kind, digest = _stat_sha(path)
    if kind == "missing":
        return MISSING, None, None
    if kind == "odd":
        return UNKNOWN, None, None
    for style, fp in fps.items():
        if fp and (fp, digest) in builds:
            return PATCHED, style, digest
    for style, want in (wants or {}).items():
        if want == digest:
            return PATCHED, style, digest
    if digest in known:
        return STALE, None, digest
    return UNKNOWN, None, digest


class Plan:
    """What is on disk (rows) and what could not be proven (problems); `built` is filled by build_plan()."""

    def __init__(self, rows, problems, vehicle, stray_dirs, fps):
        self.rows, self.problems, self.vehicle, self.stray_dirs, self.fps = rows, problems, vehicle, stray_dirs, fps
        self.built = {}


def _rows(profile, install, known, builds, fps, wants_by_rel=None):
    rows = []
    for i, rel in enumerate(active_rels(profile)):
        wants = {st: sha256(t[i][1]) for st, t in (wants_by_rel or {}).items()}
        state, style, digest = _classify(abspath(install, rel), known, builds, fps, wants)
        rows.append(Row(rel, abspath(install, rel), state, style, True, digest))
    for rel in other_rels(profile):
        state, _, digest = _classify(abspath(install, rel), known, builds, {})
        rows.append(Row(rel, abspath(install, rel), state, None, False, digest))
    return rows


def plan(profile, install, backup_dir, want):
    """The state of everything the profile may have written, from CHEAP facts only.

    For a style: the archives' central directories (size + CRC-32 + winning archive of every pinned entry), a
    fingerprint of the build (pins, bundled art, revision, builder code) and our file's SHA-256 against what the ledger
    and `builds.log` recorded. Nothing is built or inflated, so this is what the status poll and the idle watcher tick
    run. Original reads nothing from the game. What cannot be proven cheaply stays stale or unknown, never patched;
    `build_plan` does the full proof (apply path).
    """
    known = files_patch.read_ledger(backup_dir)
    builds = read_builds(backup_dir)
    problems, fps = [], {}
    if want != "original":
        try:
            facts(profile, install)
        except Refusal as exc:
            problems.append(exc.problem)
        except Exception as exc:
            problems.append(Problem("cannot_read", "cannot read the game's archives: %s" % exc,
                                    {"file": profile.mount, "reason": str(exc)}))
        fps = _fingerprints(profile, profile.patch_styles)
    rows = _rows(profile, install, known, builds, fps)
    return Plan(rows, problems, profile.vehicle, _stray_dirs(install, backup_dir), fps)


def build_plan(profile, install, backup_dir, plan_, styles):
    """The full proof: read every pinned original (SHA-256), build the outputs of `styles` in memory, prove each one
    decodes to the intended picture and the archive reads back, then re-read the state against those exact bytes
    (a file equal to today's output is ours even when no record of it survived). Mutates and returns `plan_`;
    a refusal or a failure to build is added to `plan_.problems` and nothing is built."""
    try:
        originals = read_originals(profile, install)
        for style in styles:
            plan_.built[style] = targets(profile, profile.outputs(style, originals))
    except Refusal as exc:
        plan_.problems.append(exc.problem)
        plan_.built = {}
    except Exception as exc:                          # a bad picture, a failing encoder: shown, never a crash
        plan_.problems.append(Problem(None, "cannot build the %s overlay: %s" % ("/".join(styles), exc), {}))
        plan_.built = {}
    if plan_.built:
        known = files_patch.read_ledger(backup_dir)
        plan_.rows = _rows(profile, install, known, read_builds(backup_dir), plan_.fps, plan_.built)
    return plan_


def needs_adoption(plan_):
    """True when a file with our name could still be ours (equal to today's output): only a full build can tell."""
    return any(r.state == UNKNOWN for r in plan_.rows)


def _stray_dirs(install, backup_dir):
    """Journaled folders (created by us) that exist and are empty: Original removes them."""
    out = []
    for d in read_journal(backup_dir):
        path = abspath(install, d)
        try:
            if os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path):
                out.append(d)
        except OSError:
            pass
    return out


def is_ok(row, want):
    """True when the row needs no work for `want` ("original" or a style)."""
    if want == "original":
        return row.state in (MISSING, UNKNOWN)          # unknown is not ours: left alone
    if row.active:
        return row.state == PATCHED and row.style == want
    return row.state == MISSING


def todo(plan_, want):
    return [r for r in plan_.rows if not is_ok(r, want)]


def idle(plan_, want):
    """True when there is nothing to do for `want` (Original also has to take away folders we created)."""
    return all(is_ok(r, want) for r in plan_.rows) and not (want == "original" and plan_.stray_dirs)


def removable(plan_):
    """Work Original has: files of ours, or empty folders we created (what the watcher reacts to)."""
    return bool(ours(plan_)) or bool(plan_.stray_dirs)


def applied_style(plan_, native, default):
    """The style the files show: a patched row's, `default` for ours-but-old, else `native`."""
    for r in plan_.rows:
        if r.state == PATCHED and r.active:
            return r.style
    for r in plan_.rows:
        if r.state == STALE:
            return default
    return native


def ours(plan_):
    """Rows that hold something we wrote (what Original removes, what the watcher reacts to)."""
    return [r for r in plan_.rows if r.state in (PATCHED, STALE)]


def check(plan_, want):
    """A Problem that says why `want` cannot be reached safely, or None."""
    if want == "original":
        return None
    if plan_.problems:
        return plan_.problems[0]
    for r in plan_.rows:
        if r.state == UNKNOWN:
            return Problem("foreign_file", "%s exists and was not written by this plugin (nothing was changed)" % r.rel,
                           {"file": r.rel})
    return None


def notice(plan_):
    """Under Original: a Problem for a file that is not ours (left alone), else None."""
    for r in plan_.rows:
        if r.state == UNKNOWN:
            return Problem("foreign_file", "%s exists and was not written by this plugin (left alone)" % r.rel,
                           {"file": r.rel})
    return None


# ------------------------------------------------------------------ bookkeeping of created folders


def _journal_path(backup_dir):
    return os.path.join(backup_dir, JOURNAL)


def read_journal(backup_dir):
    try:
        with open(_journal_path(backup_dir), "rb") as f:
            raw = f.read(MAX_JOURNAL_BYTES)
    except OSError:
        return []
    out = []
    for line in raw.split(b"\n")[:-1]:                   # a torn last line is ignored
        try:
            text = line.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if JOURNAL_LINE.fullmatch(text) and ".." not in text.split("/") and text not in out:
            out.append(text)
    return out[:MAX_JOURNAL]


def _write_journal(backup_dir, dirs):
    path = _journal_path(backup_dir)
    if not dirs:
        try:
            os.remove(path)
        except OSError:
            pass
        return
    os.makedirs(backup_dir, exist_ok=True)
    files_patch.write_atomic(path, "".join(d + "\n" for d in dirs[:MAX_JOURNAL]).encode("utf-8"))


def _missing_dirs(install, rel):
    """The folders of `rel`'s parent chain that do not exist yet, shallowest first."""
    parts = rel.split("/")[:-1]
    out = []
    for i in range(1, len(parts) + 1):
        d = "/".join(parts[:i])
        if not os.path.lexists(abspath(install, d)):
            out.append(d)
    return out


def _prune_dirs(install, backup_dir):
    """Remove the journaled folders that are empty (deepest first); forget the ones that are gone."""
    dirs = read_journal(backup_dir)
    if not dirs:
        return
    keep = []
    for d in sorted(dirs, key=lambda x: -x.count("/")):
        path = abspath(install, d)
        try:
            if os.path.isdir(path) and not os.path.islink(path) and not os.listdir(path):
                os.rmdir(path)
                files_patch.fsync_dir(os.path.dirname(path))
            elif os.path.lexists(path):
                keep.append(d)
        except OSError:
            keep.append(d)
    keep.reverse()
    _write_journal(backup_dir, keep)


# ------------------------------------------------------------------ ownership (Decky runs as root)


def _is_root():
    return os.geteuid() == 0


def _owner_of(install):
    info = os.stat(install)
    return info.st_uid, info.st_gid


def _neighbour_file_mode(path):
    """The mode of a regular file next to `path` (never an executable's bits), else 0644."""
    folder = os.path.dirname(path)
    try:
        for name in sorted(os.listdir(folder)):
            other = os.path.join(folder, name)
            if other != path and not name.endswith(TMP_SUFFIX) and os.path.isfile(other) and not os.path.islink(other):
                return (stat.S_IMODE(os.stat(other).st_mode) & 0o666) or 0o644
    except OSError:
        pass
    return 0o644


def _dir_mode(parent):
    try:
        return stat.S_IMODE(os.stat(parent).st_mode) or 0o755
    except OSError:
        return 0o755


def file_preparer(install, path):
    """`prepare(tmp)` for files_patch.write_atomic, or None when not root: as root the file would be root-owned, which
    Steam (running as the user) could not replace or delete later; so it gets the game folder's owner and a neighbour's mode
    BEFORE the rename."""
    if not _is_root():
        return None
    uid, gid = _owner_of(install)
    mode = _neighbour_file_mode(path)

    def prepare(tmp):
        os.chown(tmp, uid, gid)
        os.chmod(tmp, mode)
    return prepare


def own_new_dir(install, path):
    """As root, hand a folder we just created to the game folder's owner (mode copied from its parent)."""
    if not _is_root():
        return
    uid, gid = _owner_of(install)
    os.chown(path, uid, gid)
    os.chmod(path, _dir_mode(os.path.dirname(path)))


# ------------------------------------------------------------------ writing


def clean_temps(profile, install, backup_dir):
    """Remove temp files a crashed run left behind (only the names this module creates)."""
    stale = [files_patch.ledger_path(backup_dir) + TMP_SUFFIX, _journal_path(backup_dir) + TMP_SUFFIX]
    for rel in active_rels(profile, "gro") + active_rels(profile, "loose"):
        stale.append(abspath(install, rel) + TMP_SUFFIX)
    for tmp in stale:
        try:
            os.remove(tmp)
        except OSError:
            pass


def estimate_bytes(profile):
    """An upper bound of what the overlay writes, from the PINNED sizes (outputs are the originals' size, give or take
    a compressed stream) plus a megabyte for the zip: known before anything is built."""
    return sum(e["size"] for e in profile.entries) + (1 << 20)


def check_room(profile, install):
    """Refuse (RuntimeError) when the game folder cannot hold the overlay: cheap, so a full disk costs no build."""
    need = estimate_bytes(profile) + MIN_FREE
    if shutil.disk_usage(install).free < need:
        raise RuntimeError("not enough free space in the game folder (%d MB needed)" % (need >> 20))


def _need_room(install, sizes):
    total = sum(sizes)
    if shutil.disk_usage(install).free < total + MIN_FREE:
        raise RuntimeError("not enough free space in the game folder (%d MB needed)" % ((total + MIN_FREE) >> 20))


def _remove(row):
    """Delete a file of ours, after checking it is still the bytes we classified (never a file that changed)."""
    kind, digest = _stat_sha(row.path)
    if kind == "missing":
        return
    if kind != "file" or digest != row.sha:
        raise RuntimeError("%s changed while the plugin was working; it was left alone" % row.rel)
    os.remove(row.path)
    files_patch.fsync_dir(os.path.dirname(row.path))
    if os.path.lexists(row.path):
        raise RuntimeError("%s could not be removed" % row.rel)


def apply(profile, install, backup_dir, plan_, style):
    """Write `style`: every active file that is not already today's output (plan() must have built it)."""
    built = plan_.built.get(style)
    if built is None:
        raise RuntimeError("the %s overlay has not been built" % style)
    data_by_rel = dict(built)
    writes = [(r, data_by_rel[r.rel]) for r in plan_.rows if r.active and not is_ok(r, style)]
    leftovers = [r for r in plan_.rows if not r.active and r.state in (PATCHED, STALE)]
    fp = plan_.fps.get(style)
    if not fp:
        raise RuntimeError("the build fingerprint of the %s overlay cannot be computed" % style)
    if writes:
        _need_room(install, [len(d) for _, d in writes])
    # ownership first: these hashes are on disk before any file of the game folder changes (also those of files that
    # already are today's bytes, so a lost ledger is rebuilt by an adopting sync)
    files_patch.record_renders(backup_dir, [sha256(d) for _, d in built], profile.revision)
    # which build every file of ours belongs to (also for files that are already today's bytes): cheap status later
    record_builds(backup_dir, [(fp, style, sha256(d)) for _, d in built])
    if writes:
        if profile.vehicle == "loose":
            new_dirs = []
            for r, _ in writes:
                for d in _missing_dirs(install, r.rel):
                    if d not in new_dirs:
                        new_dirs.append(d)
            if new_dirs:
                known = read_journal(backup_dir)
                _write_journal(backup_dir, known + [d for d in new_dirs if d not in known])
                for d in new_dirs:                 # shallowest first; a crash leaves a journaled empty folder Original prunes
                    os.makedirs(abspath(install, d), exist_ok=True)
                    own_new_dir(install, abspath(install, d))
        for r, data in writes:
            prepare = file_preparer(install, r.path)
            if prepare is None:
                files_patch.write_atomic(r.path, data)
            else:
                files_patch.write_atomic(r.path, data, prepare=prepare)
            if files_patch.sha256(files_patch.read_file(r.path, len(data))) != sha256(data):
                raise RuntimeError("%s: the file does not hold the new data after writing" % r.rel)
    for r in reversed(leftovers):                        # a vehicle switch: the old artefact goes last
        _remove(r)
    if leftovers:
        _prune_dirs(install, backup_dir)


def revert(profile, install, backup_dir, plan_):
    """Remove everything of ours (reverse order); folders we created go too when empty. Nothing else is touched."""
    for r in reversed(ours(plan_)):
        _remove(r)
    _prune_dirs(install, backup_dir)
