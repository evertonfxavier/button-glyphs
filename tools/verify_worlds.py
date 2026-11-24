"""The fake worlds that tools/verify_profile.py runs its probes in (one class per strategy kind).

A *world* is a throw-away HOME with a fake Steam library, a fake /proc, the plugin's own settings and
runtime folders, and a copy of the game's files built from the user's real copies in `<dir>`. The REAL
Engine and the REAL profile run in it; nothing outside the temp folder is ever written.

Kinds (picked from the profile's flags, the same ones the engine dispatches on):
  files    `files_level`   loose files, state from SHA-256 + ledger      (Songs for a Hero)
  regions  `region_level`  byte ranges of a giant archive, in place     (Quantum Break, Saint Seiya)
  entries  `entry_level`   forge archives, new entry appended + index   (Assassin's Creed)
  il       (none)          a few bytes of one executable, trailer       (Charlie Murder)
  config   `config_level`  one value in a save inside the Proton prefix (10 Second Ninja X)

This module is stdlib only. It holds no game bytes: everything it builds lives in the temp folder.
"""
import builtins
import contextlib
import copy
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
sys.path.insert(0, str(ROOT / "py_modules"))
sys.path.insert(0, str(TOOLS))

import clone_validate  # noqa: E402
from button_glyphs import engine, entries, files_patch, region_patch, steam  # noqa: E402
from button_glyphs.forge import ALIGN  # noqa: E402

GLYPHS = ROOT / "py_modules" / "button_glyphs" / "glyphs"
TMP_SUFFIX = engine.TMP_SUFFIX
SECTOR = 4096                       # a write up to this size is atomic on a disk; bigger ones can be torn
SENTINEL = 4096
FULL_HASH_LIMIT = 64 << 20          # archives up to this size are hashed whole; bigger ones by their data extents
SMALL_COPY = 8 << 20                # below this, shutil.copyfile; above, `cp -c` (APFS clone)
MAX_EXTRA_FILES = 500
NEIGHBOUR_MAX = 8 << 20          # a neighbour bigger than this is not copied (it is hashed after every step)


class SourceError(Exception):
    """The user's folder does not hold what the profile needs (messages are plain English)."""


class WriteAttempt(Exception):
    """Raised by a forbidden write primitive: the engine must not have written here."""


class Crash(Exception):
    """A simulated power cut (in-process variant)."""


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def diff_count(a, b, chunk=1 << 20):
    """Number of positions where two byte strings differ (chunked: only differing chunks are walked)."""
    n = 0
    for i in range(0, max(len(a), len(b)), chunk):
        x, y = a[i:i + chunk], b[i:i + chunk]
        if x != y:
            n += abs(len(x) - len(y)) + sum(1 for p, q in zip(x, y) if p != q)
    return n


def fast_clone(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.path.getsize(src) < SMALL_COPY:
        shutil.copyfile(src, dst)
    else:
        clone_validate.clone(Path(src), dst)


def trailer_hash(path):
    """SHA-256 of a file; for one that carries our trailer, of everything before it plus the trailer minus its
    `applied` time stamp and `buildid` (two applies a second apart, or on two build ids, must not look different)."""
    trailer = engine.read_trailer(path)
    if trailer is None:
        return sha_file(path)
    h = hashlib.sha256()
    left = os.path.getsize(path) - ALIGN
    with open(path, "rb") as f:
        while left > 0:
            block = f.read(min(1 << 20, left))
            h.update(block)
            left -= len(block)
    meta = {k: v for k, v in trailer.items() if k not in ("applied", "buildid")}
    h.update(json.dumps(meta, sort_keys=True).encode("utf-8"))
    return "T:" + h.hexdigest()


def tree_of(root, hasher=None):
    """{relative path: SHA-256} of EVERY entry under root. Links show as 'LINK->target', odd things as 'NONREG'."""
    hasher = hasher or sha_file
    out = {}
    root = str(root)
    for base, dirs, names in os.walk(root):
        for d in list(dirs):
            p = os.path.join(base, d)
            if os.path.islink(p):
                out[os.path.relpath(p, root).replace(os.sep, "/")] = "LINK->" + os.readlink(p)
                dirs.remove(d)
        for n in names:
            p = os.path.join(base, n)
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            if os.path.islink(p):
                out[rel] = "LINK->" + os.readlink(p)
            elif os.path.isfile(p):
                st = os.stat(p)
                if st.st_size > (1 << 30) and getattr(st, "st_blocks", st.st_size) * 512 < st.st_size // 2:
                    out[rel] = "SPARSE:%d" % st.st_size            # a huge hole-y file is never read
                else:
                    out[rel] = hasher(p)
            else:
                out[rel] = "NONREG"
    return out


def temps_under(*roots):
    found = []
    for root in roots:
        for base, _, names in os.walk(str(root)):
            found += [os.path.join(base, n) for n in names if n.endswith(".tmp")]
    return sorted(found)


def write_manifest(steamapps, appid, name, installdir, flags, buildid):
    with open(os.path.join(str(steamapps), "appmanifest_%s.acf" % appid), "w", encoding="utf-8") as f:
        f.write('"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"%s"\n\t"StateFlags"\t\t"%s"\n'
                '\t"installdir"\t\t"%s"\n\t"buildid"\t\t"%s"\n}\n' % (appid, name, flags, installdir, buildid))


# ----------------------------------------------------------------------------------------- profiles


class CachedProfile:
    """The real profile with `render` memoised by (unit key, style, SHA-256 of the original bytes).

    A render is a pure function of those, so repeating it adds time and no information; the first call
    of each kind still runs the profile's own code. Everything else is delegated untouched.
    """

    def __init__(self, base):
        self._base = base
        self._cache = {}
        self.render_calls = 0

    def __getattr__(self, name):
        return getattr(self._base, name)

    def render(self, original, unit, style):
        key = (unit["key"], style, hashlib.sha256(original).digest())
        hit = self._cache.get(key)
        if hit is None:
            self.render_calls += 1
            hit = self._base.render(original, unit, style)
            self._cache[key] = hit
        return hit


def fresh_profile(profile, art_dir=None):
    """A shallow copy of the profile with empty picture/mask/icon caches, optionally reading its art from `art_dir`."""
    p = copy.copy(profile)
    for attr in ("pictures", "masks"):
        old = getattr(profile, attr, None)
        if old is not None and hasattr(old, "cache"):
            setattr(p, attr, type(old)(art_dir or old.mask_dir))
    if hasattr(profile, "_icons"):
        p._icons = {}
        if art_dir:
            p.glyph_dir = str(art_dir)
    return p


def art_refs(profile):
    """[(folder, name)] of every PNG the profile draws from (`name` without .png, relative to its folder)."""
    refs = []
    if getattr(profile, "files_level", False):
        folder = profile.pictures.mask_dir
        refs += [(folder, e["image"]) for e in profile.files]
    elif getattr(profile, "region_level", False) and hasattr(profile, "masks"):
        from button_glyphs.profiles.region_glyphs import LABEL, SYMBOL
        for region in profile.regions:
            spec = region.get("replace")
            if spec:
                refs.append((profile.pictures.mask_dir, spec["image"]))
            for cell in region["cells"]:
                name = SYMBOL[cell["role"]] if cell["kind"] in ("disc", "letter") else LABEL[cell["role"]]
                refs.append((profile.masks.mask_dir, name))
    elif getattr(profile, "region_level", False) and hasattr(profile, "layouts"):
        for region in profile.regions:
            for cell in profile.layouts[region["layout"]]:
                refs += [(profile.glyph_dir, name) for _, name in cell["draws"]]
    seen, out = set(), []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def copy_art(profile, dest):
    """Copy every PNG the profile uses into `dest` (same relative names). Returns [(name, folder)]."""
    dest = Path(dest)
    for folder, name in art_refs(profile):
        target = dest / (name + ".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.path.join(str(folder), name + ".png"), target)
    return art_refs(profile)


def victim_art(profile):
    """The name of the picture to damage: the LAST one drawn (so a bad one is met after earlier units)."""
    refs = art_refs(profile)
    if getattr(profile, "region_level", False) and hasattr(profile, "masks"):
        pics = [name for region in profile.regions if region.get("replace")
                for name in [region["replace"]["image"]]]
        if pics:
            return pics[-1]
    return refs[-1][1] if refs else None


def tweak_png(path):
    """Change the first non-transparent pixel of a PNG (colour AND alpha), so any render of it changes."""
    from button_glyphs import image
    rgba, w, h = image.read_png(str(path))
    rgba = bytearray(rgba)
    for i in range(0, len(rgba), 4):
        if rgba[i + 3]:
            rgba[i] = (rgba[i] + 40) & 255
            rgba[i + 3] ^= 0x40 if rgba[i + 3] > 0x40 else 0
            break
    image.write_png(str(path), bytes(rgba), w, h)


# ----------------------------------------------------------------------------------------- crashes


class CrashInjector:
    """Makes the k-th write event in a scope die, either as an exception or as a hard `os._exit(9)`.

    Write events (counted in order): every `write()` on a file opened for writing under the scope, every
    `truncate()`, and every `os.replace()` whose destination is under the scope. It patches the builtin
    `open` and `os.replace`, so it sees the writes of ANY implementation (an atomic temp+rename one and a
    sloppy in-place one alike). A write of more than one disk sector is torn (the first half is written,
    flushed, then the process dies); a smaller one is atomic on a disk, so it dies just before.
    `k=None` only counts. After the run `.count` is the number of events seen.
    """

    def __init__(self, roots, k=None, hard=False):
        self.roots = [os.path.abspath(str(r)) for r in roots]
        self.k, self.hard, self.count = k, hard, 0
        self._stack = contextlib.ExitStack()

    def in_scope(self, path):
        if not isinstance(path, (str, os.PathLike)):
            return False
        p = os.path.abspath(os.fspath(path))
        return any(p == r or p.startswith(r.rstrip("/") + "/") for r in self.roots)

    def event(self, kind, data=None, handle=None):
        n = self.count
        self.count += 1
        if self.k is None or n != self.k:
            return
        if kind == "write" and data is not None and len(data) > SECTOR and handle is not None:
            handle.write(data[:len(data) // 2])
            handle.flush()
        if self.hard:
            os._exit(9)
        raise Crash("power cut at write event %d (%s)" % (n, kind))

    def __enter__(self):
        real_open, real_replace, inj = builtins.open, os.replace, self

        class _File:
            def __init__(self, f, path):
                self._f, self._path = f, path

            def write(self, data):
                inj.event("write", data, self._f)
                return self._f.write(data)

            def truncate(self, *a):
                inj.event("truncate")
                return self._f.truncate(*a)

            def __enter__(self):
                self._f.__enter__()
                return self

            def __exit__(self, *exc):
                return self._f.__exit__(*exc)

            def __iter__(self):
                return iter(self._f)

            def __getattr__(self, name):
                return getattr(self._f, name)

        def fake_open(file, mode="r", *a, **kw):
            f = real_open(file, mode, *a, **kw)
            if isinstance(file, (str, os.PathLike)) and any(c in mode for c in "wa+x") and inj.in_scope(file):
                return _File(f, file)
            return f

        def fake_replace(src, dst, *a, **kw):
            if inj.in_scope(dst):
                inj.event("replace")
            return real_replace(src, dst, *a, **kw)

        self._stack.enter_context(mock.patch.object(builtins, "open", fake_open))
        self._stack.enter_context(mock.patch.object(os, "replace", fake_replace))
        return self

    def __exit__(self, *exc):
        self._stack.close()
        return False


def forbid_writes(targets):
    """Patch each (object, attribute) so that calling it raises WriteAttempt; `.hits` counts the attempts."""
    stack = contextlib.ExitStack()

    class Forbid:
        hits = 0

    forbid = Forbid()

    def deny(*a, **kw):
        forbid.hits += 1
        raise WriteAttempt("a write was attempted")

    for obj, attr in targets:
        original = obj.__dict__.get(attr) if isinstance(obj, type) else getattr(obj, attr)
        replacement = staticmethod(deny) if isinstance(original, staticmethod) else deny
        stack.enter_context(mock.patch.object(obj, attr, replacement))
    forbid.stack = stack
    return forbid


# ----------------------------------------------------------------------------------------- worlds


class World:
    """One fake Steam home with the game installed from the user's copies, and a real Engine over it."""

    def __init__(self, ctx, name, profile=None, media=False, buildid=None, flags="4"):
        self.ctx = ctx
        self.kind = ctx.kind
        self.appid = ctx.appid
        self.name = name
        self.root = Path(ctx.work) / name
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        self.profile = profile or ctx.profile
        self.buildid = buildid or ctx.buildid
        self.media = None
        game = ctx.profile.name
        if media:
            self.media = self.root / "run" / "media"
            lib = self.media / "deck" / "LX512"
            home_steam = self.root / ".local" / "share" / "Steam"
            (home_steam / "steamapps").mkdir(parents=True)
            (lib / "steamapps" / "common" / ctx.installdir).mkdir(parents=True)
            (home_steam / "steamapps" / "libraryfolders.vdf").write_text(
                '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
                % (home_steam, lib))
            write_manifest(lib / "steamapps", self.appid, game, ctx.installdir, flags, self.buildid)
            os.symlink(str(lib), str(self.media / "LX512"))
            self.install = lib / "steamapps" / "common" / ctx.installdir
            self.steamapps = lib / "steamapps"
        else:
            self.install = clone_validate.make_library(self.root, self.appid, game, ctx.installdir, self.buildid)
            self.steamapps = self.install.parent.parent
            if flags != "4":
                write_manifest(self.steamapps, self.appid, game, ctx.installdir, flags, self.buildid)
        self.proc = self.root / "proc"
        self.proc.mkdir()
        self.settings = self.root / "settings"
        self.runtime = self.root / "runtime"
        self.kind.materialise(self)
        self.eng = self.engine()
        self.start = self.tree()

    # ---- engine and status
    def engine(self, profile=None):
        return engine.Engine(str(self.root), str(self.settings), str(self.runtime), str(GLYPHS),
                             profiles={self.appid: profile or self.profile}, proc_root=str(self.proc))

    def status(self, eng=None):
        return next(g for g in (eng or self.eng).list_games() if g["appid"] == self.appid)

    def set(self, style, eng=None):
        return (eng or self.eng).set_style(self.appid, style)

    def sync_all(self, eng=None):
        return (eng or self.eng).sync_all("watch")

    def sync(self, eng=None):
        return (eng or self.eng).sync(self.appid, reason="watch")

    def cfg(self, eng=None):
        return (eng or self.eng).load_config()["games"].get(self.appid, {})

    # ---- files and state
    def tree(self):
        return self.kind.tree(self)

    def temps(self):
        return temps_under(self.install, self.runtime, self.settings)

    def game_temps(self):
        """Temp files in the GAME's folders (install folder, Proton prefix); the plugin's own folders are not here."""
        return temps_under(self.install, self.root / ".local" / "share" / "Steam" / "steamapps" / "compatdata")

    def plugin_temps(self):
        return temps_under(self.runtime, self.settings)

    @property
    def backup_root(self):
        return self.runtime / "backups" / self.appid

    def backups_exist(self):
        return self.backup_root.exists()

    def write_manifest(self, flags="4", buildid=None):
        if buildid:
            self.buildid = buildid
        write_manifest(self.steamapps, self.appid, self.ctx.profile.name, self.ctx.installdir, flags, self.buildid)

    # ---- the game running
    def start_game(self, line):
        d = self.proc / "4242"
        d.mkdir(exist_ok=True)
        (d / "cmdline").write_bytes(line.encode("utf-8").replace(b" ", b"\0"))

    def stop_game(self):
        shutil.rmtree(self.proc / "4242", ignore_errors=True)

    def forbid(self):
        return forbid_writes(self.kind.forbid_targets(self))

    # ---- patched copies of the tree for references are made by the ctx
    def media_patch(self):
        """Context manager: steam.MEDIA_ROOT points at this world's fake /run/media (alias spellings work)."""
        return mock.patch.object(steam, "MEDIA_ROOT", str(self.media))


@contextlib.contextmanager
def forbidding(world):
    f = world.forbid()
    with f.stack:
        yield f


# ----------------------------------------------------------------------------------------- kinds


class Kind:
    name = "kind"
    has_art = False
    unit_restore = True          # Steam "verify" can be simulated by putting a unit back
    unit_noun = "file"

    def __init__(self, profile, args):
        self.profile = profile
        self.args = args
        self.notes = []

    # ---- source
    def resolve(self, src_dir):
        raise NotImplementedError

    def units(self):
        raise NotImplementedError

    def pinned_bytes(self):
        return 0

    def materialise(self, world):
        raise NotImplementedError

    # ---- state
    hasher = staticmethod(sha_file)
    backup_before_check = False      # the engine writes a backup (in its own folder) before it refuses a file
    note_has_old = True              # last_event after an update names both build ids ("old -> new")

    def tree(self, world):
        return tree_of(world.install, self.hasher)

    def key(self, unit):
        return unit

    def expected_changed(self, world, style):
        """Units that must differ from the start tree once `style` is applied."""
        return set(self.units())

    def restore_unit(self, world, unit):
        raise NotImplementedError

    def corrupt_cases(self, world):
        return []

    def check_markers(self, world, style, tree):
        return []

    def check_backups(self, world):
        return []

    def forbid_targets(self, world):
        raise NotImplementedError

    def crash_scopes(self, world):
        """{scope name: [path roots]} the crash probe can aim at (the first is the main one)."""
        return {"install": [str(world.install)]}

    def unit_ok(self, world, unit, tree, start, patched):
        k = self.key(unit)
        return tree.get(k) in (start.get(k), patched.get(k))

    def pinned(self, src_dir):
        """(matched, total, [problems]) of the user's copies against the profile's pinned table."""
        raise NotImplementedError


class FilesKind(Kind):
    name = "files"
    has_art = True

    def resolve(self, src_dir):
        src = Path(src_dir)
        entries_ = self.profile.files
        strip = ""                                   # what to cut from an install-relative path to find it in src
        if not all((src / e["file"]).is_file() for e in entries_):
            prefix = self.profile.file.rstrip("/") + "/"
            if all(e["file"].startswith(prefix) and (src / e["file"][len(prefix):]).is_file() for e in entries_):
                strip = prefix
                self.notes.append("folder taken as the profile's %s/ folder" % self.profile.file)
            else:
                missing = [e["file"] for e in entries_ if not (src / e["file"]).is_file()
                           and not (src / e["file"][len(prefix):]).is_file()]
                raise SourceError("missing in %s: %s" % (src, ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")))
        files = {e["file"]: src / e["file"][len(strip):] for e in entries_}
        # the neighbours: other small files in the folders the profile writes into (they must stay identical)
        for folder in {Path(p).parent for p in files.values()}:
            for p in sorted(folder.iterdir())[:MAX_EXTRA_FILES]:
                if p.is_file() and not p.is_symlink() and p.stat().st_size <= NEIGHBOUR_MAX:
                    files.setdefault(strip + p.relative_to(src).as_posix(), p)
        self.files = files
        return files

    def units(self):
        return [e["file"] for e in self.profile.files]

    def pinned_bytes(self):
        return sum(e["size"] for e in self.profile.files)

    def materialise(self, world):
        for rel, src in self.files.items():
            fast_clone(src, world.install / rel)

    def entry(self, unit):
        return next(e for e in self.profile.files if e["file"] == unit)

    def restore_unit(self, world, unit):
        fast_clone(self.files[unit], world.install / unit)

    def pinned(self, src_dir):
        bad, ok = [], 0
        for e in self.profile.files:
            p = self.files.get(e["file"])
            data = read_bytes(p) if p else b""
            if len(data) != e["size"]:
                bad.append("%s: %d bytes, pinned %d" % (e["key"], len(data), e["size"]))
            elif sha_bytes(data) != e["sha256"]:
                bad.append("%s: SHA-256 differs from the pinned table" % e["key"])
            else:
                ok += 1
        return ok, len(self.profile.files), bad

    def backup_dir(self, world):
        return str(world.backup_root / "files")

    def check_markers(self, world, style, tree):
        problems = []
        ledger = files_patch.read_ledger(self.backup_dir(world))
        for e in self.profile.files:
            now = read_bytes(world.install / e["file"])
            orig = read_bytes(self.files[e["file"]])
            if len(now) != len(orig):
                problems.append("%s: size changed" % e["key"])
            if now[:e["header"]] != orig[:e["header"]]:
                problems.append("%s: header changed" % e["key"])
            if now != orig and sha_bytes(now) not in ledger:
                problems.append("%s: render hash not in the ownership ledger" % e["key"])
        return problems

    def check_backups(self, world):
        problems = []
        for e in self.profile.files:
            bak = files_patch.backup_file(self.backup_dir(world), e)
            if not os.path.exists(bak) or read_bytes(bak) != read_bytes(self.files[e["file"]]):
                problems.append("%s: backup is not the original" % e["key"])
        return problems

    def forbid_targets(self, world):
        return [(files_patch, "write_atomic")]

    def crash_scopes(self, world):
        bdir = self.backup_dir(world)
        return {"install": [str(world.install)],
                "ledger": [os.path.join(bdir, files_patch.LEDGER), os.path.join(bdir, files_patch.LEDGER + TMP_SUFFIX)],
                "backup": [str(world.runtime)]}

    def corrupt_cases(self, world):
        e = self.profile.files[0]
        size, hdr = e["size"], e["header"]
        return [("pixel byte", e["file"], hdr + (size - hdr) // 2),
                ("header byte", e["file"], max(1, hdr // 2)),
                ("last byte", e["file"], size - 1)]


class RegionsKind(Kind):
    name = "regions"
    has_art = True
    unit_noun = "region"

    def resolve(self, src_dir):
        src = Path(src_dir)
        regions = self.profile.regions
        found, missing = {}, []
        for i, r in enumerate(regions):
            flat = r["key"].replace("/", "_")
            cands = [src / (flat + ".bin"), src / flat, src / (r["key"] + ".bin"), src / r["key"]]
            path = next((c for c in cands if c.is_file()), None)
            if path is None:
                legacy = src / ("r%d.bin" % i)
                if legacy.is_file():
                    path = legacy
                    if "regions read as r<i>.bin in profile order" not in self.notes:
                        self.notes.append("regions read as r<i>.bin in profile order")
            if path is None:
                missing.append(r["key"])
            else:
                found[r["key"]] = path
        if missing:
            raise SourceError("no region file in %s for: %s (name them <key with / as _>.bin)"
                              % (src, ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")))
        self.files = found
        end = max(r["offset"] + r["size"] for r in regions)
        self.total = int(self.args.archive_size) if getattr(self.args, "archive_size", None) else end + (1 << 20)
        if self.total < end:
            raise SourceError("--archive-size %d is smaller than the last region's end (%d)" % (self.total, end))
        self._layout()
        return found

    def _layout(self):
        """Seeded random sentinels around every region plus probes in the holes (never inside a region)."""
        rnd = random.Random(1234)
        blocks = [(r["offset"], r["offset"] + r["size"]) for r in self.profile.regions]
        self.sentinels = []

        def free(o):
            return 0 <= o and o + SENTINEL <= self.total and all(o + SENTINEL <= b or o >= e for b, e in blocks)

        def add(o):
            if free(o):
                blocks.append((o, o + SENTINEL))
                self.sentinels.append((o, bytes(rnd.getrandbits(8) for _ in range(SENTINEL))))

        add(0)
        add(self.total - SENTINEL)
        for r in self.profile.regions:
            add(r["offset"] - SENTINEL)
            add(r["offset"] + r["size"])
        self.holes = []
        tries = 0
        while len(self.holes) < 96 and tries < 5000:
            tries += 1
            o = rnd.randrange(0, max(1, self.total - SENTINEL))
            if free(o):
                self.holes.append(o)

    def units(self):
        return [r["key"] for r in self.profile.regions]

    def pinned_bytes(self):
        return sum(r["size"] for r in self.profile.regions)

    def region(self, key):
        return next(r for r in self.profile.regions if r["key"] == key)

    def key(self, unit):
        return "region:" + unit

    def archive(self, world):
        return world.install / self.profile.file

    def materialise(self, world):
        path = self.archive(world)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            f.truncate(self.total)
            for off, data in self.sentinels:
                f.seek(off)
                f.write(data)
            for r in self.profile.regions:
                f.seek(r["offset"])
                f.write(read_bytes(self.files[r["key"]]))

    def tree(self, world):
        path = self.archive(world)
        out = {}
        with open(path, "rb") as f:
            for r in self.profile.regions:
                f.seek(r["offset"])
                out["region:" + r["key"]] = sha_bytes(f.read(r["size"]))
            for off, data in self.sentinels:
                f.seek(off)
                out["sentinel:%d" % off] = "ok" if f.read(len(data)) == data else "CHANGED"
            zero = bytes(SENTINEL)
            clean = True
            for o in self.holes:
                f.seek(o)
                clean = clean and f.read(SENTINEL) == zero
            out["holes"] = "zero" if clean else "DIRTY"
        out["size"] = str(os.path.getsize(path))
        out["files"] = ",".join(sorted(os.path.relpath(os.path.join(b, n), world.install)
                                       for b, _, ns in os.walk(world.install) for n in ns))
        if self.total <= FULL_HASH_LIMIT:
            out["archive"] = sha_file(path)
        return out

    def restore_unit(self, world, unit):
        r = self.region(unit)
        with open(self.archive(world), "r+b") as f:
            f.seek(r["offset"])
            f.write(read_bytes(self.files[unit]))

    def pinned(self, src_dir):
        bad, ok = [], 0
        for r in self.profile.regions:
            data = read_bytes(self.files[r["key"]])
            if len(data) != r["size"]:
                bad.append("%s: %d bytes, pinned %d" % (r["key"], len(data), r["size"]))
            elif sha_bytes(data) != r["sha256"]:
                bad.append("%s: SHA-256 differs from the pinned table" % r["key"])
            else:
                ok += 1
        return ok, len(self.profile.regions), bad

    def backup_dir(self, world):
        return str(world.backup_root / "regions")

    def check_markers(self, world, style, tree):
        problems = []
        with open(self.archive(world), "rb") as f:
            for r in self.profile.regions:
                orig = read_bytes(self.files[r["key"]])
                f.seek(r["offset"])
                now = f.read(r["size"])
                if len(now) != len(orig):
                    problems.append("%s: size changed" % r["key"])
                    continue
                marker = region_patch.parse_marker(now[:region_patch.HEADER])
                want = ("P", style, self.profile.revision, bytes.fromhex(r["sha256"])[:16])
                if marker != want:
                    problems.append("%s: marker %r, expected %r" % (r["key"], marker, want))
                lo, hi = region_patch.MARK_OFF, region_patch.MARK_OFF + region_patch.MARK_LEN
                if now[:lo] != orig[:lo] or now[hi:region_patch.HEADER] != orig[hi:region_patch.HEADER]:
                    problems.append("%s: header changed outside the marker" % r["key"])
        return problems

    def check_backups(self, world):
        problems = []
        for r in self.profile.regions:
            bak = region_patch.backup_file(self.backup_dir(world), r)
            if not os.path.exists(bak) or read_bytes(bak) != read_bytes(self.files[r["key"]]):
                problems.append("%s: backup is not the original" % r["key"])
        return problems

    def forbid_targets(self, world):
        return [(region_patch, "_write"), (region_patch, "ensure_backups")]

    def unit_ok(self, world, unit, tree, start, patched):
        k = self.key(unit)
        if tree.get(k) in (start.get(k), patched.get(k)):
            return True
        r = self.region(unit)
        with open(self.archive(world), "rb") as f:
            f.seek(r["offset"])
            marker = region_patch.parse_marker(f.read(region_patch.HEADER))
        return marker is not None and marker[0] == "W"          # a region being written: recoverable

    def corrupt_cases(self, world):
        r = self.profile.regions[0]
        return [("body byte", r["key"], region_patch.HEADER + (r["size"] - region_patch.HEADER) // 2),
                ("header byte", r["key"], 4),
                ("last byte", r["key"], r["size"] - 1)]


class CopyKind(Kind):
    """Kinds that need only whole files copied from the user's folder (entries, il, config)."""

    def _files(self):
        return clone_validate.profile_files(self.profile)

    def resolve(self, src_dir):
        src = Path(src_dir)
        missing = [f for f in self._files() if not (src / f).is_file()]
        if missing:
            raise SourceError("missing in %s: %s" % (src, ", ".join(missing)))
        self.files = {f: src / f for f in self._files()}
        return self.files

    def units(self):
        return self._files()

    def pinned_bytes(self):
        return sum(os.path.getsize(p) for p in self.files.values())

    def materialise(self, world):
        for rel, src in self.files.items():
            fast_clone(src, world.install / rel)

    def restore_unit(self, world, unit):
        fast_clone(self.files[unit], world.install / unit)

    def forbid_targets(self, world):
        return [(engine.Engine, "_copy")]


class EntriesKind(CopyKind):
    name = "entries"
    hasher = staticmethod(trailer_hash)

    def expected_changed(self, world, style):
        return {t["file"] for t in entries.wanted(self.profile, style)}

    def pinned(self, src_dir):
        problems = []
        for t in self.profile.targets():
            problems += self.profile.check_target(str(self.files[t["file"]]), t)
        total = len(self.profile.targets())
        return (total if not problems else 0), total, problems

    def check_markers(self, world, style, tree):
        problems = []
        want = self.expected_changed(world, style)
        for t in self.profile.targets():
            path = str(world.install / t["file"])
            state, trailer = entries.inspect(path)
            if t["file"] in want:
                if state != entries.PATCHED or trailer.get("style") != style \
                        or trailer.get("revision") != self.profile.revision:
                    problems.append("%s: state %s, trailer %r" % (t["file"], state, trailer and trailer.get("style")))
            elif state != entries.ORIGINAL:
                problems.append("%s: should be original, is %s" % (t["file"], state))
        return problems

    def corrupt_cases(self, world):
        return [("file cut in half", self.units()[0], "truncate"), ("magic zeroed", self.units()[0], "magic")]

    def unit_ok(self, world, unit, tree, start, patched):
        if tree.get(unit) in (start.get(unit), patched.get(unit)):
            return True
        return entries.inspect(str(world.install / unit))[0] in (entries.PARTIAL, entries.HALF)

    def crash_scopes(self, world):
        return {"install": [str(world.install)]}


class IlKind(CopyKind):
    name = "il"
    hasher = staticmethod(trailer_hash)
    backup_before_check = True
    note_has_old = False             # the note names the new build only when the original file is unchanged

    def pinned(self, src_dir):
        problems = self.profile.check(str(self.files[self.profile.file]))
        return (0 if problems else 1), 1, problems

    def expected_changed(self, world, style):
        return {self.profile.file}

    def check_markers(self, world, style, tree):
        path = str(world.install / self.profile.file)
        orig = read_bytes(self.files[self.profile.file])
        trailer = engine.read_trailer(path)
        problems = []
        if not trailer or trailer.get("style") != style or trailer.get("revision") != self.profile.revision:
            problems.append("trailer %r" % (trailer and trailer.get("style")))
        now = read_bytes(path)[:len(orig)]
        differing = diff_count(orig, now)
        allowed = len(self.profile.edits[style])
        if not differing or differing > allowed:
            problems.append("%d bytes differ from the original, the profile edits %d" % (differing, allowed))
        return problems

    def check_backups(self, world):
        bak = world.eng.backup_path(world.appid, self.profile)
        if not os.path.exists(bak) or read_bytes(bak) != read_bytes(self.files[self.profile.file]):
            return ["backup is not the original"]
        return []

    def corrupt_cases(self, world):
        data = read_bytes(self.files[self.profile.file])
        m = self.profile.pattern.search(data)
        return [("first byte of the code pattern", self.profile.file, m.start() if m else 0)]


class ConfigKind(CopyKind):
    name = "config"
    unit_restore = False

    def resolve(self, src_dir):
        src = Path(src_dir)
        p = self.profile
        build = self.args.buildid if getattr(self.args, "buildid", None) not in (None, "0") else None
        if build is None and len(p.builds) == 1:
            build = next(iter(p.builds))
        if build not in p.builds:
            raise SourceError("build %s is not pinned by the profile (use --buildid)" % build)
        self.build = build
        self.data_rel = p.builds[build]["file"]
        missing = [f for f in (self.data_rel, p.file) if not (src / f).is_file()]
        if missing:
            raise SourceError("missing in %s: %s" % (src, ", ".join(missing)))
        self.files = {self.data_rel: src / self.data_rel}
        self.save_src = src / p.file
        return self.files

    def units(self):
        return [self.profile.file]

    def key(self, unit):
        return "@save"

    def pinned_bytes(self):
        return os.path.getsize(self.files[self.data_rel])

    def save_path(self, world):
        return world.root / ".local" / "share" / "Steam" / "steamapps" / "compatdata" / world.appid / "pfx" / self.profile.file

    def materialise(self, world):
        CopyKind.materialise(self, world)
        dst = self.save_path(world)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.save_src, dst)

    def tree(self, world):
        out = tree_of(world.install)
        out["@save"] = sha_file(self.save_path(world))
        return out

    def start_value(self):
        return self.profile.locate(read_bytes(self.save_src))[1]

    def expected_changed(self, world, style):
        return {self.profile.file} if self.start_value() != self.profile.values[style] else set()

    def restore_unit(self, world, unit):
        shutil.copyfile(self.save_src, self.save_path(world))

    def pinned(self, src_dir):
        problems = self.profile.check_build(self.build, str(Path(src_dir)), full=True)
        try:
            self.profile.locate(read_bytes(self.save_src))
        except Exception as exc:                        # ConfigValueError and OSError alike
            problems.append("save: %s" % exc)
        return (0 if problems else 2), 2, problems

    def check_markers(self, world, style, tree):
        problems = []
        path = self.save_path(world)
        value = self.profile.locate(read_bytes(path))[1]
        if value != self.profile.values[style]:
            problems.append("the save holds %r, expected %r" % (value, self.profile.values[style]))
        rec = world.eng._read_record(world.appid, self.profile, world.cfg())
        if rec is None or rec.get("original_value") != self.start_value():
            problems.append("record does not hold the value found before the first change")
        return problems

    def check_backups(self, world):
        bak = world.eng._config_backup_path(world.appid, self.profile)
        if not os.path.exists(bak) or read_bytes(bak) != read_bytes(self.save_src):
            return ["the copy of the save is not the pristine save"]
        return []

    def forbid_targets(self, world):
        return [(engine.Engine, "_copy"), (engine.Engine, "_write_value"),
                (engine.Engine, "_store_record"), (engine.Engine, "_drop_record")]

    def crash_scopes(self, world):
        return {"state": [str(self.save_path(world)), str(world.backup_root)]}

    def unit_ok(self, world, unit, tree, start, patched):
        if tree.get("@save") in (start.get("@save"), patched.get("@save")):
            return True
        pristine, now = read_bytes(self.save_src), read_bytes(self.save_path(world))
        off = self.profile.locate(pristine)[0]
        width = len(next(iter(self.profile.values.values())))
        blank = lambda b: b[:off] + b"?" * width + b[off + width:]          # noqa: E731
        return len(now) == len(pristine) and blank(now) == blank(pristine)

    def corrupt_cases(self, world):
        return [("data file byte", self.data_rel, "flip"), ("save key renamed", self.profile.file, "key")]


KINDS = {"files": FilesKind, "regions": RegionsKind, "entries": EntriesKind, "il": IlKind, "config": ConfigKind}


def kind_name(profile):
    for flag, name in (("files_level", "files"), ("region_level", "regions"),
                       ("config_level", "config"), ("entry_level", "entries")):
        if getattr(profile, flag, False):
            return name
    return "il"


# ----------------------------------------------------------------------------------------- corrupting


def apply_corruption(world, case):
    """Make the damage of one `corrupt_cases` entry in the world's pristine files."""
    label, unit, how = case
    kind = world.kind
    if kind.name == "regions":
        r = kind.region(unit)
        path, off = kind.archive(world), r["offset"] + how
    elif kind.name == "config":
        if how == "key":
            path = kind.save_path(world)
            data = read_bytes(path)
            i = data.index(b"buttons=")
            with open(path, "r+b") as f:
                f.seek(i)
                f.write(b"buttonz=")
            return
        path, off = world.install / unit, os.path.getsize(world.install / unit) // 2
    elif how == "truncate":
        path = world.install / unit
        with open(path, "r+b") as f:
            f.truncate(os.path.getsize(path) // 2)
        return
    elif how == "magic":
        with open(world.install / unit, "r+b") as f:
            f.write(b"\0" * 16)
        return
    else:
        path, off = world.install / unit, how
    with open(path, "r+b") as f:
        f.seek(off)
        b = f.read(1)
        f.seek(off)
        f.write(bytes([b[0] ^ 1]))


def make_symlink_case(world, kind_label, unit):
    """Replace a game file by a link / directory / oversized sparse file; returns the outside path it points at."""
    p = world.install / unit
    outside = world.root / "outside.bin"
    shutil.copyfile(p, outside)
    p.unlink()
    if kind_label == "symlink to a copy":
        os.symlink(str(outside), str(p))
    elif kind_label == "symlink to a directory":
        (world.root / "dd").mkdir()
        os.symlink(str(world.root / "dd"), str(p))
    elif kind_label == "directory":
        p.mkdir()
    else:                                                # oversized sparse file
        with open(p, "wb") as f:
            f.truncate(3 << 30)
    return outside


def run_child(spec, timeout=600):
    """Run tools/verify_crash_child.py with a JSON spec; returns the CompletedProcess."""
    import json
    child = TOOLS / "verify_crash_child.py"
    return subprocess.run([sys.executable, str(child), json.dumps(spec)], capture_output=True, text=True,
                          timeout=timeout)


def new_tempdir():
    return Path(os.path.realpath(tempfile.mkdtemp(prefix="bg-verify-")))



def load_profiles(factory=None, arg=None):
    """{appid: profile}: the registry, or what `module:function(arg)` returns (tests use their own profiles)."""
    if not factory:
        return clone_validate.PROFILES
    import importlib
    module, _, func = factory.partition(":")
    return getattr(importlib.import_module(module), func)(arg)
