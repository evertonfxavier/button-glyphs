"""Files-level patching: tiny loose files rewritten whole, state read from SHA-256 (no marker).

Everything runs on a synthetic Content tree of fake XNB files (built with the test_xnb builder) and
tiny fake pictures; a fake Steam library has an install folder with accents like the real game's.
"""
import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

from button_glyphs import engine, files_patch, image, xnb  # noqa: E402
from button_glyphs.profiles.region_glyphs import PictureLoader  # noqa: E402
from button_glyphs.profiles.xnb_pictures import XnbPictureProfile  # noqa: E402
from test_backend import GLYPH_DIR, make_library  # noqa: E402
from test_xnb import build  # noqa: E402

APPID = "389170"
INSTALLDIR = "A Lenda do Herói - O Jogo"
# (key, width, height): the textures the profile draws; the others are left alone
DRAWN = [("Tooltip/A", 4, 3), ("Tooltip/B", 4, 4), ("Tooltip/Dpad", 5, 5),
         ("Menu/xbox/a", 3, 3), ("Menu/xbox/lbumper", 6, 4), ("Menu/xbox/start", 5, 4)]
UNTOUCHED = [("Tooltip/Hold", 4, 2), ("Menu/xbox/dup", 3, 3), ("Tooltip/LEFT_STICK_UP", 4, 4)]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def original_xnb(key, w, h):
    """A fake original with the real build's layout (header ends at 85) and pixels unique per file."""
    seed = sum(key.encode("utf-8")) & 255
    block = bytearray()
    for y in range(h):
        for x in range(w):
            block += bytes(((x * 37 + y * 11 + seed) & 255, (x * 5 + y * 61 + seed) & 255,
                            (x * 23 + y * 7) & 255, 255 if (x + y) % 3 else 0))
    return build(w, h, block=bytes(block), filler=1)


def art_rgba(w, h, seed=1):
    """A drawing the size of the texture: alpha 0 or 255, transparent pixels (0,0,0,0)."""
    out = bytearray()
    for y in range(h):
        for x in range(w):
            if (x * 3 + y + seed) % 4 == 0:
                out += b"\0\0\0\0"
            else:
                out += bytes((200 - x * 9 - seed, 30 + y * 20, 90 + seed * 7, 255))
    return bytes(out)


def tree(root):
    """{relative path: sha256} of EVERY file under root (temp files included)."""
    out = {}
    for base, _, names in os.walk(root):
        for name in names:
            path = os.path.join(base, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, root)] = sha(f.read())
    return out


class Crash(Exception):
    """A power cut at a chosen moment."""


class FilesCase(unittest.TestCase):
    """A fake game with 6 drawn and 3 untouched textures plus unrelated files."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        common = make_library(self.home, [(APPID, "Songs for a Hero - Definitive Edition", INSTALLDIR, "4")])
        self.install = os.path.join(common, INSTALLDIR)
        self.content = os.path.join(self.install, "Content")
        self.art_dir = os.path.join(self.home, "art")
        os.makedirs(os.path.join(self.art_dir, "t"))
        self.entries = []
        for key, w, h in DRAWN:
            data = original_xnb(key, w, h)
            self.write_game_file(key, data)
            name = "t/" + key.replace("/", "_")
            image.write_png(os.path.join(self.art_dir, name + ".png"), art_rgba(w, h), w, h)
            tex = xnb.parse(data)
            self.entries.append({
                "key": key, "file": "Content/%s.xnb" % key, "width": w, "height": h,
                "header": tex.data_offset, "size": len(data), "sha256": sha(data), "image": name})
        for key, w, h in UNTOUCHED:
            self.write_game_file(key, original_xnb(key, w, h))
        os.makedirs(os.path.join(self.content, "Other"))
        with open(os.path.join(self.content, "Other", "readme.txt"), "wb") as f:
            f.write(b"not a texture")
        with open(os.path.join(self.install, "DumaLegend.exe"), "wb") as f:
            f.write(os.urandom(300))
        self.profile = self.make_profile()
        self.pristine = tree(self.install)
        self.proc = os.path.join(self.home, "proc")
        os.makedirs(self.proc)
        self.eng = self.new_engine()

    def tearDown(self):
        shutil.rmtree(self.home)

    # ---- helpers
    def make_profile(self):
        return XnbPictureProfile(
            appid=APPID, name="Songs for a Hero - Definitive Edition", content_dir="Content",
            files=self.entries, roles=[("a", "A / ✕")], native_style="xbox", revision=1,
            pictures=PictureLoader(self.art_dir))

    def new_engine(self):
        return engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={APPID: self.profile}, proc_root=self.proc)

    def write_game_file(self, key, data):
        path = os.path.join(self.content, *(key + ".xnb").split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def path(self, key):
        return os.path.join(self.content, *(key + ".xnb").split("/"))

    def read(self, key):
        with open(self.path(key), "rb") as f:
            return f.read()

    def original(self, key):
        """The pristine bytes of a game file, taken from the fixture build (not from the disk)."""
        w, h = next((w, h) for k, w, h in DRAWN + UNTOUCHED if k == key)
        return original_xnb(key, w, h)

    def drawn(self, key):
        """What the profile must produce: the original with the drawing as its pixel block."""
        w, h = next((w, h) for k, w, h in DRAWN if k == key)
        return xnb.with_pixels(self.original(key), art_rgba(w, h))

    def tree(self):
        return tree(self.install)

    def status(self):
        return next(g for g in self.eng.list_games() if g["appid"] == APPID)

    def backup_dir(self):
        return os.path.join(self.eng.backup_root, APPID, "files")

    def change_art(self, seed=5):
        for e in self.entries:
            image.write_png(os.path.join(self.art_dir, e["image"] + ".png"),
                            art_rgba(e["width"], e["height"], seed), e["width"], e["height"])
        self.profile.pictures = PictureLoader(self.art_dir)    # a new process would read the new files

    def backups(self):
        """{name: sha256} of the .orig files only (the ledger changes with every apply)."""
        return {k: v for k, v in tree(self.backup_dir()).items() if k.endswith(".orig")}

    def patched_tree(self):
        """The tree a clean apply gives (computed from the fixture, not by running the engine)."""
        expected = dict(self.pristine)
        for e in self.entries:
            rel = os.path.relpath(self.path(e["key"]), self.install)
            expected[rel] = sha(self.drawn(e["key"]))
        return expected

    def start_game(self, windows_path=False):
        pid = os.path.join(self.proc, "123")
        os.makedirs(pid, exist_ok=True)
        where = self.install + "/DumaLegend.exe"
        if windows_path:
            where = "Z:" + where.replace("/", "\\")
        with open(os.path.join(pid, "cmdline"), "wb") as f:
            f.write(("reaper SteamLaunch AppId=%s -- %s" % (APPID, where)).encode("utf-8").replace(b" ", b"\0"))

    def stop_game(self):
        shutil.rmtree(os.path.join(self.proc, "123"))

    def crash_after(self, k, under):
        """write_atomic works k times for paths under `under`, then the 'power' goes."""
        real = files_patch.write_atomic
        count = [0]

        def wrapper(path, data):
            if path.startswith(under):
                if count[0] >= k:
                    raise Crash("power cut before write %d" % (k + 1))
                count[0] += 1
            return real(path, data)
        return mock.patch.object(files_patch, "write_atomic", wrapper)

    def forbid_writes(self):
        def wrapper(path, data):
            raise AssertionError("nothing may be written, but %s was" % path)
        return mock.patch.object(files_patch, "write_atomic", wrapper)


class RenderTest(FilesCase):
    def test_render_is_deterministic_length_preserving_and_keeps_the_header(self):
        for e in self.entries:
            original = self.original(e["key"])
            a = self.profile.render(original, e, "ps")
            self.assertEqual(a, self.profile.render(original, e, "ps"))
            self.assertEqual(len(a), len(original))
            self.assertEqual(a[:e["header"]], original[:e["header"]])
            self.assertEqual(a, self.drawn(e["key"]))
            self.assertEqual(xnb.pixels(a), art_rgba(e["width"], e["height"]))
            self.assertNotEqual(a, original)

    def test_render_refuses_what_it_does_not_know(self):
        e = self.entries[0]
        original = self.original(e["key"])
        with self.assertRaises(ValueError):
            self.profile.render(original, e, "xbox")             # not a style it draws
        with self.assertRaises(ValueError):
            self.profile.render(original[:-4], e, "ps")          # wrong size
        other = dict(e, width=e["width"] + 1)
        with self.assertRaises(ValueError):
            self.profile.render(original, other, "ps")           # not this texture

    def test_table_mistakes_fail_at_construction(self):
        with self.assertRaises(ValueError):
            XnbPictureProfile(APPID, "t", "Content", self.entries + [dict(self.entries[0])], [], "xbox")
        with self.assertRaises(ValueError):
            XnbPictureProfile(APPID, "t", "Content", [dict(self.entries[0], file="../x.xnb")], [], "xbox")
        with self.assertRaises(ValueError):
            XnbPictureProfile(APPID, "t", "Content", [dict(self.entries[0], size=10)], [], "xbox")
        with self.assertRaises(ValueError):                    # two keys, one file
            XnbPictureProfile(APPID, "t", "Content", self.entries + [dict(self.entries[0], key="Other/A")],
                              [], "xbox")
        big = dict(self.entries[0], width=1500, height=1500, size=85 + 1500 * 1500 * 4)
        with self.assertRaises(ValueError):                    # far larger than a tiny texture
            XnbPictureProfile(APPID, "t", "Content", [big], [], "xbox")


class ApplyAndUndoTest(FilesCase):
    def test_ps_draws_only_the_listed_files_and_keeps_every_size(self):
        r = self.eng.set_style(APPID, "ps")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["changed"])
        for e in self.entries:
            self.assertEqual(self.read(e["key"]), self.drawn(e["key"]), e["key"])
        for key, _, _ in UNTOUCHED:                         # the other textures are not ours
            self.assertEqual(self.read(key), self.original(key))
        after = self.tree()
        gone_or_new = {rel for rel in set(after) | set(self.pristine) if after.get(rel) != self.pristine.get(rel)}
        self.assertEqual(gone_or_new, {os.path.relpath(self.path(e["key"]), self.install) for e in self.entries})
        self.assertEqual(after, self.patched_tree())
        st = self.status()
        self.assertEqual((st["state"], st["applied"]), ("ok", "ps"))
        self.assertEqual(st["message"], "PlayStation glyphs active")

    def test_whole_tree_is_identical_after_ps_original_ps_original(self):
        for style in ("ps", "original", "ps", "original"):
            self.assertTrue(self.eng.set_style(APPID, style)["ok"], style)
            self.assertEqual(self.tree(), self.patched_tree() if style == "ps" else self.pristine, style)
        self.assertEqual(self.tree(), self.pristine)         # every file, nothing left behind

    def test_native_style_is_the_same_as_original(self):
        self.eng.set_style(APPID, "ps")
        self.assertTrue(self.eng.set_style(APPID, "xbox")["ok"])
        self.assertEqual(self.tree(), self.pristine)
        st = self.status()
        self.assertEqual((st["state"], st["applied"], st["message"]), ("ok", "xbox", "Xbox glyphs active"))

    def test_second_apply_and_second_original_write_nothing(self):
        self.eng.set_style(APPID, "ps")
        patched = self.tree()
        with self.forbid_writes():
            r = self.eng.set_style(APPID, "ps")
            self.assertTrue(r["ok"], r)
            self.assertFalse(r["changed"])
            self.assertIn("already applied", " ".join(r["logs"]))
        self.assertEqual(self.tree(), patched)
        self.eng.set_style(APPID, "original")
        with self.forbid_writes():
            r = self.eng.set_style(APPID, "original")
            self.assertTrue(r["ok"] and not r["changed"], r)
        self.assertEqual(self.tree(), self.pristine)

    def test_original_before_anything_is_a_no_op(self):
        with self.forbid_writes():
            r = self.eng.set_style(APPID, "original")
        self.assertTrue(r["ok"] and not r["changed"])
        self.assertEqual(self.tree(), self.pristine)
        self.assertEqual(self.status()["state"], "ok")

    def test_backups_are_the_whole_original_files_and_survive_original(self):
        self.eng.set_style(APPID, "ps")
        bdir = self.backup_dir()
        names = sorted(n for n in os.listdir(bdir) if n != files_patch.LEDGER)
        self.assertEqual(names, sorted(e["key"].replace("/", "_") + ".orig" for e in self.entries))
        for e in self.entries:
            with open(os.path.join(bdir, e["key"].replace("/", "_") + ".orig"), "rb") as f:
                self.assertEqual(f.read(), self.original(e["key"]))
        self.eng.set_style(APPID, "original")
        self.assertEqual(sorted(n for n in os.listdir(bdir) if n != files_patch.LEDGER), names)   # never deleted
        self.assertTrue(self.status()["has_backup"])

    def test_a_bad_backup_blocks_the_restore_and_is_replaced_once_the_file_is_original(self):
        self.eng.set_style(APPID, "ps")
        bad = os.path.join(self.backup_dir(), "Tooltip_A.orig")
        with open(bad, "wb") as f:
            f.write(b"garbage")
        patched = self.tree()
        with self.forbid_writes():                            # the patched file has no verified backup
            r = self.eng.set_style(APPID, "original")
        self.assertFalse(r["ok"])
        self.assertIn("Tooltip/A", r["errors"][0])
        self.assertEqual(self.tree(), patched)                # all-or-nothing: no other file was restored
        with open(self.path("Tooltip/A"), "wb") as f:         # Steam "verify files"
            f.write(self.original("Tooltip/A"))
        self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
        with open(bad, "rb") as f:
            self.assertEqual(f.read(), self.original("Tooltip/A"))    # a fresh, verified copy
        self.assertEqual(self.tree(), self.patched_tree())

    def test_verified_backups_are_never_rewritten(self):
        self.eng.set_style(APPID, "ps")
        written = []
        real = files_patch.write_atomic
        with mock.patch.object(files_patch, "write_atomic", lambda p, d: written.append(p) or real(p, d)):
            self.eng.set_style(APPID, "original")
            self.eng.set_style(APPID, "ps")
        ledger = files_patch.ledger_path(self.backup_dir())
        self.assertEqual(len(written), 2 * len(self.entries) + 1)          # 6 restores, the ledger, 6 drawings
        self.assertEqual([p for p in written if not p.startswith(self.install)], [ledger])
        self.assertFalse([p for p in written if p.endswith(".orig")])      # no backup is touched

    def test_a_backup_of_the_right_size_but_other_bytes_is_not_trusted(self):
        self.eng.set_style(APPID, "ps")
        bad = os.path.join(self.backup_dir(), "Menu_xbox_a.orig")
        with open(bad, "rb") as f:
            data = bytearray(f.read())
        data[-1] ^= 0xFF                                      # same size, one flipped bit pattern
        with open(bad, "wb") as f:
            f.write(data)
        patched = self.tree()
        with self.forbid_writes():
            r = self.eng.set_style(APPID, "original")
        self.assertFalse(r["ok"])
        self.assertIn("Menu/xbox/a", r["errors"][0])
        self.assertEqual(self.tree(), patched)                # the wrong bytes were never written to the game
        self.assertIsNone(files_patch.backup_data(self.backup_dir(), self.entries[3]))

    def test_no_temp_file_is_left_and_permissions_are_kept(self):
        os.chmod(self.path("Tooltip/A"), 0o640)
        self.eng.set_style(APPID, "ps")
        self.assertEqual(os.stat(self.path("Tooltip/A")).st_mode & 0o777, 0o640)
        for base, _, names in os.walk(self.home):
            self.assertEqual([n for n in names if n.endswith(files_patch.TMP_SUFFIX)], [])


class SteamRestoresTest(FilesCase):
    """Steam 'verify files' / an update puts originals back; the watcher draws them again."""

    def test_verify_files_is_treated_as_original_and_reapplied(self):
        self.eng.set_style(APPID, "ps")
        patched = self.tree()
        for e in self.entries:
            with open(self.path(e["key"]), "wb") as f:
                f.write(self.original(e["key"]))
        st = self.status()
        self.assertEqual(st["state"], "pending")
        results = self.eng.sync_all("watch")
        self.assertTrue(results and results[0]["changed"], results)
        self.assertEqual(self.tree(), patched)                # deterministic output
        self.assertEqual(self.status()["state"], "ok")

    def test_partly_restored_tree_is_completed(self):
        self.eng.set_style(APPID, "ps")
        patched = self.tree()
        for key in ("Tooltip/B", "Menu/xbox/a"):
            with open(self.path(key), "wb") as f:
                f.write(self.original(key))
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual([r.state for r in rows],
                         [files_patch.PATCHED, files_patch.ORIGINAL, files_patch.PATCHED,
                          files_patch.ORIGINAL, files_patch.PATCHED, files_patch.PATCHED])
        self.eng.sync_all("watch")
        self.assertEqual(self.tree(), patched)

    def test_choosing_ps_on_a_half_patched_tree_completes_it(self):
        self.eng.set_style(APPID, "ps")
        for key in ("Tooltip/B", "Menu/xbox/start"):
            with open(self.path(key), "wb") as f:
                f.write(self.original(key))
        self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
        self.assertEqual(self.tree(), self.patched_tree())

    def test_a_settings_loss_does_not_matter(self):
        self.eng.set_style(APPID, "ps")
        os.remove(self.eng.config_path)                       # state lives in the files, not in config
        fresh = self.new_engine()
        st = next(g for g in fresh.list_games() if g["appid"] == APPID)
        self.assertEqual((st["style"], st["applied"]), ("original", "ps"))
        self.assertEqual(st["state"], "pending")              # wants Original, files hold ps
        self.assertTrue(fresh.set_style(APPID, "original")["ok"])
        self.assertEqual(self.tree(), self.pristine)


class StaleTest(FilesCase):
    """The art (or the profile) changed after the install: old drawings are rebuilt from the backup."""

    def test_old_drawing_is_stale_not_unknown_and_is_rebuilt_from_the_backup(self):
        self.eng.set_style(APPID, "ps")
        old_backups = self.backups()
        self.change_art()
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual({r.state for r in rows}, {files_patch.STALE})
        st = self.status()
        self.assertEqual(st["state"], "pending")
        self.assertEqual(st["message"], "Improved PlayStation glyphs will be applied")
        results = self.eng.sync_all("watch")
        self.assertTrue(results[0]["ok"] and results[0]["changed"], results)
        for e in self.entries:                                 # built from the ORIGINAL, never stacked
            expected = xnb.with_pixels(self.original(e["key"]), art_rgba(e["width"], e["height"], 5))
            self.assertEqual(self.read(e["key"]), expected, e["key"])
        self.assertEqual(self.backups(), old_backups)            # backups untouched
        self.assertEqual(self.status()["state"], "ok")
        self.eng.set_style(APPID, "original")
        self.assertEqual(self.tree(), self.pristine)

    def test_a_render_that_uses_the_original_is_always_fed_the_pristine_one(self):
        """Styles never stack: even a render that blends with the old pixels sees the original."""
        class Blend(XnbPictureProfile):
            def render(self, original, entry, style):
                art = XnbPictureProfile.render(self, original, entry, style)
                mixed = bytes(a ^ b for a, b in zip(xnb.pixels(art), xnb.pixels(original)))
                return xnb.with_pixels(original, mixed)

        self.profile = Blend(
            appid=APPID, name="t", content_dir="Content", files=self.entries, roles=[],
            native_style="xbox", pictures=PictureLoader(self.art_dir))
        self.eng = self.new_engine()
        self.eng.set_style(APPID, "ps")
        self.change_art()
        self.profile.pictures = PictureLoader(self.art_dir)
        self.eng.sync_all("watch")
        for e in self.entries:
            original = self.original(e["key"])
            art = art_rgba(e["width"], e["height"], 5)
            expected = xnb.with_pixels(original, bytes(a ^ b for a, b in zip(art, xnb.pixels(original))))
            self.assertEqual(self.read(e["key"]), expected, e["key"])
        self.eng.set_style(APPID, "original")
        self.assertEqual(self.tree(), self.pristine)

    def test_original_restores_stale_files_too(self):
        self.eng.set_style(APPID, "ps")
        self.change_art()
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(self.tree(), self.pristine)

    def test_stale_files_under_original_say_restored_not_improved(self):
        self.eng.set_style(APPID, "ps")
        self.change_art()
        cfg = self.eng.load_config()
        cfg["games"][APPID]["style"] = "original"             # chosen while the game ran: not synced yet
        self.eng.save_config(cfg)
        st = self.status()
        self.assertEqual((st["state"], st["message"]), ("pending", "Original will be restored"))
        cfg["games"][APPID]["style"] = "xbox"
        self.eng.save_config(cfg)
        self.assertEqual(self.status()["message"], "Xbox glyphs will be restored")

    def test_only_the_stale_files_are_rewritten(self):
        self.eng.set_style(APPID, "ps")
        e = self.entries[2]                                    # one picture changes
        image.write_png(os.path.join(self.art_dir, e["image"] + ".png"),
                        art_rgba(e["width"], e["height"], 9), e["width"], e["height"])
        self.profile.pictures = PictureLoader(self.art_dir)
        written = []
        real = files_patch.write_atomic
        with mock.patch.object(files_patch, "write_atomic", lambda p, d: written.append(p) or real(p, d)):
            self.eng.sync_all("watch")
        self.assertEqual([p for p in written if p.startswith(self.install)], [self.path(e["key"])])

    def test_stale_file_whose_backup_is_gone_is_refused_and_nothing_is_written(self):
        self.eng.set_style(APPID, "ps")
        self.change_art()
        os.remove(os.path.join(self.backup_dir(), "Menu_xbox_a.orig"))
        before = self.tree()
        with self.forbid_writes():
            r = self.eng.sync(APPID)
        self.assertFalse(r["ok"])
        self.assertIn("the backup of the original is missing", r["errors"][0])   # ours (ledger), cannot be undone
        self.assertIn("Menu/xbox/a", r["errors"][0])
        self.assertIn("Verify files", r["errors"][0])
        self.assertEqual(self.tree(), before)                  # the other 5 stale files were not touched
        st = self.status()
        self.assertEqual(st["state"], "error")

    def test_a_stale_file_with_a_different_header_is_another_build(self):
        self.eng.set_style(APPID, "ps")
        self.change_art()
        e = self.entries[0]
        path = self.path(e["key"])
        data = bytearray(self.read(e["key"]))
        data[4] = 0x55                                         # inside the header: not our kind of change
        with open(path, "wb") as f:
            f.write(data)
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual(rows[0].state, files_patch.UNKNOWN)
        self.assertEqual({r.state for r in rows[1:]}, {files_patch.STALE})


class RefusalTest(FilesCase):
    def test_unknown_build_is_refused_with_every_file_untouched(self):
        with open(self.path("Menu/xbox/lbumper"), "r+b") as f:
            f.seek(100)
            f.write(b"\xAB\xCD")                               # another build of the texture
        before = self.tree()
        with self.forbid_writes():
            r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("not supported", r["errors"][0])
        self.assertIn("Menu/xbox/lbumper", r["errors"][0])
        self.assertEqual(self.tree(), before)                  # not one byte, not one backup
        self.assertFalse(os.path.exists(self.backup_dir()))
        st = self.status()
        self.assertEqual(st["state"], "error")                 # an error outranks "pending"
        self.assertNotIn("will be applied", st["message"])

    def test_missing_file_is_refused(self):
        os.remove(self.path("Tooltip/Dpad"))
        before = self.tree()
        r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("Tooltip/Dpad: file not found", r["errors"][0])
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.status()["state"], "error")

    def test_wrong_size_symlink_and_folder_are_unknown_not_read(self):
        with open(self.path("Tooltip/A"), "ab") as f:
            f.write(b"\0")                                     # truncated/extended: size differs
        elsewhere = os.path.join(self.content, "Other", "b.xnb")          # same bytes as the original
        with open(elsewhere, "wb") as f:
            f.write(self.original("Tooltip/B"))
        os.remove(self.path("Tooltip/B"))
        os.symlink(elsewhere, self.path("Tooltip/B"))
        os.remove(self.path("Menu/xbox/a"))
        os.makedirs(self.path("Menu/xbox/a"))
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual([r.state for r in rows],
                         [files_patch.UNKNOWN, files_patch.UNKNOWN, files_patch.ORIGINAL,
                          files_patch.UNKNOWN, files_patch.ORIGINAL, files_patch.ORIGINAL])

    def test_original_leaves_files_that_are_not_ours_alone(self):
        self.eng.set_style(APPID, "ps")
        newer = original_xnb("Tooltip/A", 5, 3)                 # another build: other size and header
        with open(self.path("Tooltip/A"), "wb") as f:
            f.write(newer)
        self.assertEqual(self.status()["state"], "error")       # for ps: not the supported build
        r = self.eng.set_style(APPID, "original")
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.read("Tooltip/A"), newer)         # the file that is not ours stays
        for e in self.entries[1:]:
            self.assertEqual(self.read(e["key"]), self.original(e["key"]))
        st = self.status()                                      # but it does not read as a plain "ok"
        self.assertEqual(st["state"], "error")
        self.assertIn("1 of 6 files are not the supported build", st["message"])
        self.assertIn("Verify files", st["message"])

    def test_lost_backups_are_an_error_and_nothing_is_touched(self):
        self.eng.set_style(APPID, "ps")
        shutil.rmtree(self.backup_dir())
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual({(r.state, r.style) for r in rows}, {(files_patch.ORPHAN, "ps")})
        before = self.tree()
        for style in ("original", "ps"):
            with self.forbid_writes():
                r = self.eng.set_style(APPID, style)
            self.assertFalse(r["ok"], style)
            self.assertIn("backup of the original is missing", r["errors"][0])
            self.assertEqual(self.tree(), before)
        self.assertEqual(self.status()["state"], "error")
        # Steam > Verify files makes them originals again: the plugin recovers by itself
        for e in self.entries:
            with open(self.path(e["key"]), "wb") as f:
                f.write(self.original(e["key"]))
        self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
        self.assertEqual(self.tree(), self.patched_tree())

    def test_a_render_with_the_wrong_length_or_header_is_refused_before_any_write(self):
        good, entries = self.profile, self.entries

        class Bad(object):
            files, patch_styles, revision = entries, ("ps",), 1

            def __init__(self, mode):
                self.mode = mode

            def render(self, original, entry, style):
                if self.mode == "short":
                    return original[:-1]
                if self.mode == "header":
                    return b"\xFF" + original[1:]                # header changed
                if entry is entries[-1]:                        # "late": only the LAST file goes wrong
                    raise ValueError("cannot draw this one")
                return good.render(original, entry, style)

        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        files_patch.ensure_backups(self.profile, self.install, self.backup_dir(), rows)
        before = self.tree()
        for mode in ("short", "header", "late"):
            with self.assertRaises((RuntimeError, ValueError)):
                files_patch.apply(Bad(mode), self.install, self.backup_dir(), rows, "ps")
            self.assertEqual(self.tree(), before, mode)       # nothing was written, not even the first files

    def test_a_file_that_changes_between_the_check_and_the_backup_is_not_backed_up(self):
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        with open(self.path("Tooltip/B"), "r+b") as f:             # e.g. Steam started an update
            f.seek(100)
            f.write(b"\x01")
        with self.assertRaises(RuntimeError):
            files_patch.ensure_backups(self.profile, self.install, self.backup_dir(), rows)
        self.assertFalse(os.path.exists(os.path.join(self.backup_dir(), "Tooltip_B.orig")))

    def corrupting_writes(self):
        """Writes that do not hold the bytes they were given (a failing card)."""
        real = files_patch.write_atomic
        return mock.patch.object(
            files_patch, "write_atomic",
            lambda path, data: real(path, data[:-1] + bytes((data[-1] ^ 1,))) if path.startswith(self.install)
            else real(path, data))

    def test_a_write_that_does_not_read_back_is_an_error(self):
        with self.corrupting_writes():
            r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("does not hold the new data", r["errors"][0])
        self.assertEqual(self.status()["state"], "error")

    def test_a_restore_that_does_not_read_back_is_an_error(self):
        self.eng.set_style(APPID, "ps")
        with self.corrupting_writes():
            r = self.eng.set_style(APPID, "original")
        self.assertFalse(r["ok"])
        self.assertIn("does not match the original", r["errors"][0])

    def test_not_enough_free_space_writes_nothing(self):
        before = self.tree()
        usage = shutil.disk_usage(self.home)
        with mock.patch.object(files_patch.shutil, "disk_usage", return_value=usage._replace(free=1000)):
            r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("not enough free space", r["errors"][0])
        self.assertEqual(self.tree(), before)


class PreflightTest(FilesCase):
    """A missing or damaged drawing stops the apply before the first byte is written."""

    def assert_untouched_and_error(self, needle):
        before = self.tree()
        with self.forbid_writes():
            r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn(needle, r["errors"][0])
        self.assertEqual(self.tree(), before)
        self.assertFalse(os.path.exists(self.backup_dir()))     # not even a backup
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertIn(needle, st["message"])

    def test_missing_picture(self):
        os.remove(os.path.join(self.art_dir, "t", "Menu_xbox_start.png"))     # the LAST one
        self.assert_untouched_and_error("Menu/xbox/start")

    def test_truncated_picture(self):
        with open(os.path.join(self.art_dir, "t", "Tooltip_B.png"), "r+b") as f:
            f.truncate(40)
        self.assert_untouched_and_error("Tooltip/B")

    def test_picture_of_the_wrong_size(self):
        image.write_png(os.path.join(self.art_dir, "t", "Tooltip_A.png"), art_rgba(5, 3), 5, 3)
        self.assert_untouched_and_error("Tooltip/A")

    def test_original_still_works_with_the_art_gone(self):
        self.eng.set_style(APPID, "ps")
        shutil.rmtree(self.art_dir)
        self.profile.pictures = PictureLoader(self.art_dir)
        self.assertEqual(self.status()["state"], "pending")     # cannot tell stale from patched: pending
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(self.tree(), self.pristine)
        self.assertEqual(self.status()["state"], "ok")

    def test_ps_with_the_art_gone_after_a_patch_is_an_error_not_a_loop(self):
        self.eng.set_style(APPID, "ps")
        shutil.rmtree(self.art_dir)
        self.profile.pictures = PictureLoader(self.art_dir)
        before = self.tree()
        results = self.eng.sync_all("watch")
        self.assertFalse(results[0]["ok"])
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.status()["state"], "error")


class GuardsAndStatusTest(FilesCase):
    def test_nothing_is_written_while_the_game_runs_and_the_watcher_retries(self):
        for windows in (False, True):
            with self.subTest(windows_path=windows):
                self.start_game(windows)
                with self.forbid_writes():
                    r = self.eng.set_style(APPID, "ps")
                self.assertTrue(r["ok"] and not r["changed"])
                self.assertEqual(self.tree(), self.pristine)
                self.assertEqual(self.status()["state"], "running")
                self.stop_game()
        results = self.eng.sync_all("watch")
        self.assertTrue(results[0]["changed"])
        self.assertEqual(self.tree(), self.patched_tree())

    def test_original_chosen_while_the_game_runs_is_finished_by_the_watcher(self):
        self.eng.set_style(APPID, "ps")
        self.start_game()
        r = self.eng.set_style(APPID, "original")
        self.assertTrue(r["ok"] and not r["changed"])
        self.assertEqual(self.tree(), self.patched_tree())
        self.assertEqual(self.status()["state"], "running")
        results = self.eng.sync_all("watch")                    # the watcher knows a restore is pending...
        self.assertTrue(results and results[0]["ok"] and not results[0]["changed"])     # ...and waits
        self.assertEqual(self.tree(), self.patched_tree())
        self.stop_game()
        self.assertEqual(self.status()["state"], "pending")
        results = self.eng.sync_all("watch")
        self.assertTrue(results and results[0]["changed"], results)
        self.assertEqual(self.tree(), self.pristine)
        self.assertEqual(self.eng.sync_all("watch"), [])        # nothing left to do: the watcher is idle

    def test_a_game_that_was_never_patched_is_not_synced_by_the_watcher(self):
        self.eng.set_style(APPID, "original")
        self.assertEqual(self.eng.sync_all("watch"), [])

    def test_nothing_is_written_while_steam_updates_the_game(self):
        common = make_library(self.home, [(APPID, "Songs for a Hero - Definitive Edition", INSTALLDIR, "1026")])
        self.assertTrue(os.path.isdir(os.path.join(common, INSTALLDIR)))
        with self.forbid_writes():
            r = self.eng.set_style(APPID, "ps")
        self.assertTrue(r["ok"] and not r["changed"])
        self.assertEqual(self.status()["state"], "updating")
        self.assertEqual(self.tree(), self.pristine)

    def test_status_order_running_updating_error_pending_ok(self):
        self.assertEqual(self.status()["state"], "ok")                  # Original, untouched
        cfg = self.eng.load_config()
        cfg["games"][APPID] = {"style": "ps"}
        self.eng.save_config(cfg)
        self.assertEqual(self.status()["state"], "pending")             # wanted, not applied
        cfg["games"][APPID]["error"] = "boom"
        self.eng.save_config(cfg)
        st = self.status()
        self.assertEqual((st["state"], st["message"]), ("error", "boom"))   # error outranks pending
        make_library(self.home, [(APPID, "Songs for a Hero - Definitive Edition", INSTALLDIR, "1026")])
        self.assertEqual(self.status()["state"], "updating")            # updating outranks error
        self.start_game()
        self.assertEqual(self.status()["state"], "running")             # running outranks updating
        self.stop_game()
        make_library(self.home, [(APPID, "Songs for a Hero - Definitive Edition", INSTALLDIR, "4")])
        self.assertEqual(self.status()["state"], "error")
        del cfg["games"][APPID]["error"]
        self.eng.save_config(cfg)
        self.assertEqual(self.status()["state"], "pending")
        self.assertTrue(self.eng.sync(APPID)["ok"])
        self.assertEqual(self.status()["state"], "ok")

    def test_a_failed_sync_shows_its_error_until_the_next_good_one(self):
        with open(self.path("Tooltip/A"), "r+b") as f:
            f.seek(90)
            f.write(b"\x01")
        self.eng.set_style(APPID, "ps")
        self.assertEqual(self.status()["state"], "error")
        with open(self.path("Tooltip/A"), "wb") as f:                  # Steam > Verify files
            f.write(self.original("Tooltip/A"))
        self.assertEqual(self.status()["state"], "error")              # the stored error still shows
        self.eng.sync_all("watch")
        self.assertEqual(self.status()["state"], "ok")

    def test_revision_and_roles_reach_the_ui(self):
        st = self.status()
        self.assertEqual(st["roles"], ["A / ✕"])
        self.assertEqual(st["has_backup"], False)


class LedgerTest(FilesCase):
    """Ownership without a marker: only a file whose SHA-256 we recorded can be an older drawing of ours."""

    def pixel_update(self, key="Tooltip/A"):
        """What a game update that changes only pixels ships: same size, same header, other pixels."""
        w, h = next((w, h) for k, w, h in DRAWN if k == key)
        return original_xnb(key, w, h)[:-4] + b"\x09\x08\x07\x06"

    def test_the_render_hashes_are_on_disk_before_the_first_file_is_written(self):
        seen = []
        real = files_patch.write_atomic

        def spy(path, data):
            if path.startswith(self.install):
                seen.append(set(files_patch.read_ledger(self.backup_dir())))
            return real(path, data)
        with mock.patch.object(files_patch, "write_atomic", spy):
            self.eng.set_style(APPID, "ps")
        want = {sha(self.drawn(e["key"])) for e in self.entries}
        self.assertEqual(len(seen), len(self.entries))
        self.assertTrue(all(want <= known for known in seen), "a file was written before its hash was recorded")
        self.assertEqual(files_patch.read_ledger(self.backup_dir()), {h: 1 for h in want})
        with open(files_patch.ledger_path(self.backup_dir()), "rb") as f:
            raw = f.read()
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(len(raw.splitlines()), 6)

    def test_a_game_update_that_changes_only_pixels_is_not_ours_under_original(self):
        """The reviewer's reproduction: it used to be 'restored' to the OLD original by the watcher."""
        self.eng.set_style(APPID, "ps")
        self.eng.set_style(APPID, "original")
        update = self.pixel_update()
        with open(self.path("Tooltip/A"), "wb") as f:
            f.write(update)
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual(rows[0].state, files_patch.UNKNOWN)
        before = self.tree()
        with self.forbid_writes():
            self.assertEqual(self.eng.sync_all("watch"), [])          # nothing is pending: no loop, no write
            r = self.eng.set_style(APPID, "original")
            self.assertTrue(r["ok"] and not r["changed"], r)
        self.assertEqual(self.read("Tooltip/A"), update)
        self.assertEqual(self.tree(), before)
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertIn("1 of 6 files are not the supported build", st["message"])
        with self.forbid_writes():                                  # and PlayStation is refused, untouched
            r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("Tooltip/A: differs from the supported build", r["errors"][0])
        self.assertIn("Verify files", r["errors"][0])
        self.assertEqual(self.tree(), before)
        with open(self.path("Tooltip/A"), "wb") as f:               # Steam > Verify files
            f.write(self.original("Tooltip/A"))
        self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
        self.assertEqual(self.tree(), self.patched_tree())

    def test_a_game_update_while_playstation_is_active_is_refused_and_kept(self):
        self.eng.set_style(APPID, "ps")
        update = self.pixel_update("Menu/xbox/a")
        with open(self.path("Menu/xbox/a"), "wb") as f:
            f.write(update)
        before = self.tree()
        with self.forbid_writes():
            r = self.eng.sync(APPID)                               # the watcher's view: ps wanted
        self.assertFalse(r["ok"])
        self.assertIn("Menu/xbox/a: differs from the supported build", r["errors"][0])
        self.assertEqual(self.tree(), before)
        r = self.eng.set_style(APPID, "original")                  # ours are restored, the update is kept
        self.assertTrue(r["ok"] and r["changed"], r)
        self.assertEqual(self.read("Menu/xbox/a"), update)
        for e in self.entries:
            if e["key"] != "Menu/xbox/a":
                self.assertEqual(self.read(e["key"]), self.original(e["key"]), e["key"])
        with self.forbid_writes():
            self.assertEqual(self.eng.sync_all("watch"), [])
        self.assertEqual(self.status()["state"], "error")

    def test_the_revision_upgrade_keeps_working_and_records_both_generations(self):
        self.eng.set_style(APPID, "ps")
        self.change_art()
        self.profile.revision = 2
        self.assertEqual(self.status()["message"], "Improved PlayStation glyphs will be applied")
        self.eng.sync_all("watch")
        for e in self.entries:
            expected = xnb.with_pixels(self.original(e["key"]), art_rgba(e["width"], e["height"], 5))
            self.assertEqual(self.read(e["key"]), expected, e["key"])
        known = files_patch.read_ledger(self.backup_dir())
        self.assertEqual(sorted(known.values()), [1] * 6 + [2] * 6)
        self.eng.set_style(APPID, "original")
        self.assertEqual(self.tree(), self.pristine)

    def test_a_crash_between_the_ledger_and_the_first_file_is_harmless(self):
        with self.crash_after(0, self.install):
            r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertEqual(self.tree(), self.pristine)                # not one game file was written
        self.assertEqual(len(files_patch.read_ledger(self.backup_dir())), 6)     # hashes of renders never written
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual({r.state for r in rows}, {files_patch.ORIGINAL})   # unwritten renders own nothing
        self.eng = self.new_engine()
        self.assertTrue(self.eng.sync_all("watch")[0]["ok"])
        self.assertEqual(self.tree(), self.patched_tree())
        self.assertEqual(len(files_patch.read_ledger(self.backup_dir())), 6)     # re-recorded, not duplicated

    def test_a_crash_while_writing_the_ledger_leaves_the_game_untouched(self):
        n = len(self.entries)
        with self.crash_after(n, os.path.join(self.home, "runtime")):    # n backups, then the ledger fails
            r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertEqual(self.tree(), self.pristine)
        self.assertFalse(os.path.exists(files_patch.ledger_path(self.backup_dir())))
        self.eng = self.new_engine()
        self.eng.sync_all("watch")
        self.assertEqual(self.tree(), self.patched_tree())

    def test_a_torn_or_damaged_ledger_line_is_ignored(self):
        os.makedirs(self.backup_dir())
        good, other = sha(b"one"), sha(b"two")
        torn = sha(b"three")
        with open(files_patch.ledger_path(self.backup_dir()), "wb") as f:
            f.write(("%s 1\n" % good).encode())
            f.write(b"not a hash at all\n")
            f.write((sha(b"UPPER").upper() + " 1\n").encode())        # not lower-case hex
            f.write(("%s 1x\n" % sha(b"junk")).encode())               # not a number
            f.write(("%s 2\n" % other).encode())
            f.write(torn.encode() + b" 1")                              # a crash cut the last line: no newline
        self.assertEqual(files_patch.read_ledger(self.backup_dir()), {good: 1, other: 2})
        files_patch.record_renders(self.backup_dir(), [sha(b"four")], 3)    # the next write does not glue onto the tear
        known = files_patch.read_ledger(self.backup_dir())
        self.assertEqual(known, {good: 1, other: 2, sha(b"four"): 3})
        with open(files_patch.ledger_path(self.backup_dir()), "rb") as f:
            self.assertTrue(all(files_patch.LEDGER_LINE.fullmatch(l) for l in f.read().split(b"\n")[:-1]))

    def test_a_repeated_hash_moves_to_the_end_so_the_cap_drops_the_oldest(self):
        a, b = sha(b"a"), sha(b"b")
        files_patch.record_renders(self.backup_dir(), [a, b], 1)
        files_patch.record_renders(self.backup_dir(), [a], 2)
        self.assertEqual(list(files_patch.read_ledger(self.backup_dir()).items()), [(b, 1), (a, 2)])
        with open(files_patch.ledger_path(self.backup_dir()), "wb") as f:     # a hand-damaged file with a repeat
            f.write(("%s 1\n%s 1\n%s 3\n" % (a, b, a)).encode())
        self.assertEqual(list(files_patch.read_ledger(self.backup_dir()).items()), [(b, 1), (a, 3)])

    def test_a_file_whose_hash_is_only_on_a_torn_line_is_unknown(self):
        self.eng.set_style(APPID, "ps")
        self.change_art()
        path = files_patch.ledger_path(self.backup_dir())
        old = sha(self.read("Tooltip/A"))
        with open(path, "rb") as f:
            lines = f.read().splitlines(True)
        keep = [l for l in lines if not l.startswith(old.encode())]
        self.assertEqual(len(keep), 5)
        with open(path, "wb") as f:
            f.write(b"".join(keep) + old.encode() + b" 1")             # that line was torn
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual(rows[0].state, files_patch.UNKNOWN)
        self.assertEqual({r.state for r in rows[1:]}, {files_patch.STALE})

    def test_the_ledger_is_bounded_and_has_no_duplicates(self):
        digests = [sha(str(i).encode()) for i in range(files_patch.MAX_LEDGER + 900)]
        files_patch.record_renders(self.backup_dir(), digests, 1)
        known = files_patch.read_ledger(self.backup_dir())
        self.assertEqual(len(known), files_patch.MAX_LEDGER)
        self.assertEqual(list(known)[-1], digests[-1])                # the newest survive
        self.assertNotIn(digests[0], known)
        size = os.path.getsize(files_patch.ledger_path(self.backup_dir()))
        self.assertLess(size, files_patch.MAX_LEDGER_BYTES)
        files_patch.record_renders(self.backup_dir(), digests[-50:], 2)    # the same hashes again
        self.assertEqual(os.path.getsize(files_patch.ledger_path(self.backup_dir())), size)
        self.assertEqual(files_patch.read_ledger(self.backup_dir())[digests[-1]], 2)
        with open(files_patch.ledger_path(self.backup_dir()), "ab") as f:    # a runaway file is read only in part
            f.write(("%s 1\n" % sha(b"x")).encode() * (files_patch.MAX_LEDGER_BYTES // 60))
        self.assertLessEqual(len(files_patch.read_ledger(self.backup_dir())), files_patch.MAX_LEDGER + 1)

    def test_a_lost_ledger_turns_old_drawings_into_unknown_and_playstation_is_refused(self):
        self.eng.set_style(APPID, "ps")
        self.change_art()
        os.remove(files_patch.ledger_path(self.backup_dir()))
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual({r.state for r in rows}, {files_patch.UNKNOWN})
        before = self.tree()
        with self.forbid_writes():
            r = self.eng.sync(APPID)
        self.assertFalse(r["ok"])
        self.assertIn("differs from the supported build", r["errors"][0])
        self.assertIn("Verify files", r["errors"][0])
        self.assertEqual(self.tree(), before)
        for e in self.entries:                                       # Steam > Verify files: all well again
            with open(self.path(e["key"]), "wb") as f:
                f.write(self.original(e["key"]))
        self.assertTrue(self.eng.sync(APPID)["ok"])
        for e in self.entries:
            expected = xnb.with_pixels(self.original(e["key"]), art_rgba(e["width"], e["height"], 5))
            self.assertEqual(self.read(e["key"]), expected)

    def test_a_lost_ledger_with_unchanged_art_is_still_patched_and_original_restores(self):
        self.eng.set_style(APPID, "ps")
        os.remove(files_patch.ledger_path(self.backup_dir()))
        self.assertEqual({r.state for r in files_patch.plan(self.profile, self.install, self.backup_dir())},
                         {files_patch.PATCHED})
        self.assertEqual(self.status()["state"], "ok")
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(self.tree(), self.pristine)

    def test_a_ledger_hash_without_a_backup_is_an_error_not_unknown(self):
        self.eng.set_style(APPID, "ps")
        self.change_art()
        os.remove(os.path.join(self.backup_dir(), "Tooltip_A.orig"))
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual(rows[0].state, files_patch.ORPHAN)
        r = self.eng.set_style(APPID, "original")
        self.assertFalse(r["ok"])
        self.assertIn("cannot restore", r["errors"][0])


class OriginalWarningTest(FilesCase):
    """Under Original a file that is not ours and not the supported build is said out loud."""

    def test_plugin_data_lost_then_a_new_art_revision(self):
        """The reviewer's second reproduction: Original used to read 'ok' while PlayStation art stayed."""
        self.eng.set_style(APPID, "ps")
        shutil.rmtree(self.eng.backup_root)                        # the plugin's data is gone
        self.change_art(7)
        rows = files_patch.plan(self.profile, self.install, self.backup_dir())
        self.assertEqual({r.state for r in rows}, {files_patch.UNKNOWN})
        before = self.tree()
        with self.forbid_writes():
            r = self.eng.set_style(APPID, "original")
            self.assertTrue(r["ok"] and not r["changed"], r)
            self.assertEqual(self.eng.sync_all("watch"), [])      # the watcher has nothing to do: no loop
        self.assertEqual(self.tree(), before)
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertIn("6 of 6 files are not the supported build", st["message"])
        self.assertIn("Verify files", st["message"])
        with self.forbid_writes():
            r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("Verify files", r["errors"][0])
        for e in self.entries:                                      # Steam > Verify files
            with open(self.path(e["key"]), "wb") as f:
                f.write(self.original(e["key"]))
        self.eng.set_style(APPID, "original")
        self.assertEqual(self.status()["state"], "ok")

    def test_ours_are_restored_the_rest_is_reported_and_the_watcher_goes_quiet(self):
        self.eng.set_style(APPID, "ps")
        with open(self.path("Tooltip/B"), "wb") as f:
            f.write(original_xnb("Tooltip/B", 6, 6))                # another build
        cfg = self.eng.load_config()
        cfg["games"][APPID]["style"] = "original"
        self.eng.save_config(cfg)
        st = self.status()                                          # 5 to restore AND 1 unknown: error, not pending
        self.assertEqual(st["state"], "error")
        results = self.eng.sync_all("watch")                        # the watcher finishes what is ours...
        self.assertTrue(results and results[0]["ok"] and results[0]["changed"], results)
        for e in self.entries:
            if e["key"] != "Tooltip/B":
                self.assertEqual(self.read(e["key"]), self.original(e["key"]), e["key"])
        self.assertEqual(self.read("Tooltip/B"), original_xnb("Tooltip/B", 6, 6))
        with self.forbid_writes():                                  # ...and then does nothing, minute after minute
            for _ in range(3):
                self.assertEqual(self.eng.sync_all("watch"), [])
        self.assertEqual(self.status()["state"], "error")

    def test_an_error_outranks_pending_and_running_and_updating_outrank_the_error(self):
        self.eng.set_style(APPID, "ps")
        os.remove(self.path("Menu/xbox/start"))                     # missing counts too
        cfg = self.eng.load_config()
        cfg["games"][APPID]["style"] = "original"
        self.eng.save_config(cfg)
        self.assertEqual(self.status()["state"], "error")
        self.assertIn("1 of 6 files are not the supported build", self.status()["message"])
        self.start_game()
        self.assertEqual(self.status()["state"], "running")

    def test_an_unsupported_build_that_was_never_touched_is_a_notice_not_an_error(self):
        os.remove(self.path("Tooltip/B"))
        with open(self.path("Tooltip/A"), "ab") as f:
            f.write(b"\0")
        self.assertFalse(os.path.exists(self.backup_dir()))
        st = self.status()                                          # the plugin never handled this game
        self.assertEqual(st["state"], "ok")
        self.assertEqual(st["message"], "2 of 6 files are not the supported build (the game was probably "
                                        "updated); PlayStation cannot be applied")
        with self.forbid_writes():
            self.assertEqual(self.eng.sync_all("watch"), [])
        r = self.eng.set_style(APPID, "ps")                         # choosing PlayStation is still an error
        self.assertFalse(r["ok"])
        self.assertEqual(self.status()["state"], "error")

    def test_the_wording_is_honest_about_what_verify_files_can_do(self):
        self.eng.set_style(APPID, "ps")
        shutil.rmtree(self.eng.backup_root)                        # art left by a lost plugin install
        self.change_art(7)
        self.eng.set_style(APPID, "original")
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertEqual(st["message"], "6 of 6 files are not the supported build (a game update, or art left "
                                        "by a lost plugin install); Steam > Verify files helps only in the second case")
        self.assertNotIn("restores them", st["message"])
        r = self.eng.set_style(APPID, "ps")
        self.assertIn("helps only in the second case", r["errors"][0])
        self.assertNotIn("restores the original files", r["errors"][0])

    def test_where_verify_files_does_restore_ours_the_hint_says_so(self):
        self.eng.set_style(APPID, "ps")
        shutil.rmtree(self.backup_dir())                           # ours (equal to today's render), no backup
        r = self.eng.set_style(APPID, "original")
        self.assertIn("the backup of the original is missing", r["errors"][0])
        self.assertIn("Steam > Verify files restores the original files", r["errors"][0])
        self.assertNotIn("second case", r["errors"][0])

    def test_a_game_the_plugin_handled_before_is_an_error_even_without_its_data(self):
        self.eng.set_style(APPID, "ps")                             # the config remembers an apply
        shutil.rmtree(self.eng.backup_root)
        self.change_art(7)
        self.assertEqual(self.status()["state"], "error")
        os.remove(self.eng.config_path)                             # config AND data lost: nothing left to tell
        fresh = self.new_engine()
        st = next(g for g in fresh.list_games() if g["appid"] == APPID)
        self.assertEqual(st["state"], "ok")                         # only a notice can be given
        self.assertIn("6 of 6 files are not the supported build", st["message"])


class ReadBackTest(FilesCase):
    def test_a_backup_that_does_not_read_back_stops_everything_before_a_game_file_is_written(self):
        real = files_patch.read_file
        seen = set()

        def lying(path, size):                                      # the first read of a .orig is the read-back
            data = real(path, size)
            if path.endswith(".orig") and path not in seen:
                seen.add(path)
                return data[:-1] + bytes((data[-1] ^ 1,))
            return data
        before = self.tree()
        with mock.patch.object(files_patch, "read_file", lying):
            r = self.eng.set_style(APPID, "ps")
        self.assertFalse(r["ok"])
        self.assertIn("could not be verified", r["errors"][0])
        self.assertEqual(self.tree(), before)                      # no game file was written
        self.assertFalse(os.path.exists(files_patch.ledger_path(self.backup_dir())))

    def test_reads_are_bounded_to_size_plus_one(self):
        path = self.path("Tooltip/A")
        with open(path, "ab") as f:
            f.write(b"\0" * 100000)
        self.assertEqual(len(files_patch.read_file(path, 10)), 11)
        row = files_patch.plan(self.profile, self.install, self.backup_dir())[0]
        self.assertEqual(row.state, files_patch.UNKNOWN)

    def test_a_file_that_grows_between_the_size_check_and_the_read_is_unknown(self):
        real = files_patch.read_file
        entry = self.entries[0]
        path = self.path(entry["key"])
        grown = self.read(entry["key"]) + b"\0"
        with mock.patch.object(files_patch, "read_file", lambda p, size: real(p, size) + b"\0" if p == path else real(p, size)):
            state, _ = files_patch.classify(self.profile, entry, path, self.backup_dir(), {sha(grown): 1})
        self.assertEqual(state, files_patch.UNKNOWN)               # not "ours" even if that exact hash were recorded


class RobustnessTest(FilesCase):
    """One game's trouble never breaks the listing or the watcher of the others."""

    def test_a_plan_that_raises_is_an_error_for_this_game_only(self):
        with mock.patch.object(files_patch, "plan", side_effect=RuntimeError("boom")):
            games = self.eng.list_games()
            self.assertEqual(self.status()["state"], "error")
            self.assertIn("boom", self.status()["message"])
            cfg = self.eng.load_config()
            cfg["games"][APPID] = {"style": "original"}
            self.eng.save_config(cfg)
            self.assertEqual(self.eng.sync_all("watch"), [])
        self.assertEqual([g["appid"] for g in games], [APPID])

    def test_an_unreadable_install_folder_does_not_raise(self):
        shutil.rmtree(self.content)
        st = self.status()                                          # files missing: said, not raised
        self.assertEqual(st["state"], "ok")
        self.assertIn("6 of 6 files are not the supported build", st["message"])
        os.chmod(self.install, 0)
        try:
            self.assertEqual(self.status()["state"], "error")
        finally:
            os.chmod(self.install, 0o755)


class LoggingTest(FilesCase):
    """A real bug behind a swallowed error leaves a traceback in the plugin log; the user text stays plain."""

    class FakeLog(object):
        def __init__(self):
            self.infos, self.exceptions = [], []

        def info(self, msg, *args):
            self.infos.append(msg % args if args else msg)

        def exception(self, msg, *args):
            import sys as _sys
            self.exceptions.append((msg % args if args else msg, _sys.exc_info()[0]))

    def logged_engine(self):
        log = self.FakeLog()
        eng = engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={APPID: self.profile}, proc_root=self.proc, logger=log)
        return eng, log

    def test_status_logs_the_traceback_and_keeps_the_user_message(self):
        eng, log = self.logged_engine()
        with mock.patch.object(files_patch, "plan", side_effect=RuntimeError("boom")):
            games = eng.list_games()
        self.assertEqual(games[0]["state"], "error")
        self.assertEqual(games[0]["message"], "cannot read Content: boom")
        self.assertEqual(len(log.exceptions), 1)
        self.assertIn(APPID, log.exceptions[0][0])
        self.assertIs(log.exceptions[0][1], RuntimeError)            # called inside the except block

    def test_the_watcher_logs_the_traceback_and_goes_on(self):
        eng, log = self.logged_engine()
        cfg = eng.load_config()
        cfg["games"][APPID] = {"style": "original"}
        eng.save_config(cfg)
        with mock.patch.object(files_patch, "plan", side_effect=RuntimeError("boom")):
            self.assertEqual(eng.sync_all("watch"), [])
        self.assertEqual(len(log.exceptions), 1)
        self.assertIn(APPID, log.exceptions[0][0])
        self.assertIs(log.exceptions[0][1], RuntimeError)

    def test_no_logger_is_fine(self):
        with mock.patch.object(files_patch, "plan", side_effect=RuntimeError("boom")):
            self.assertEqual(self.status()["state"], "error")
            self.assertEqual(self.eng.sync_all("watch"), [])


class LedgerTempTest(FilesCase):
    def test_the_ledgers_own_stale_temp_is_removed_at_every_sync_and_under_original(self):
        self.eng.set_style(APPID, "ps")
        tmp = files_patch.ledger_path(self.backup_dir()) + files_patch.TMP_SUFFIX
        for style in ("ps", "original", "original"):                # a no-op sync, a restore, a no-op restore
            with open(tmp, "wb") as f:
                f.write(b"half a ledger")
            self.assertTrue(self.eng.set_style(APPID, style)["ok"], style)
            self.assertFalse(os.path.exists(tmp), style)
        self.assertEqual(self.tree(), self.pristine)

    def test_a_ledger_temp_does_not_break_the_next_ledger_write(self):
        os.makedirs(self.backup_dir())
        tmp = files_patch.ledger_path(self.backup_dir()) + files_patch.TMP_SUFFIX
        with open(tmp, "wb") as f:
            f.write(b"x" * 10)
        self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
        self.assertFalse(os.path.exists(tmp))
        self.assertEqual(len(files_patch.read_ledger(self.backup_dir())), len({
            sha(self.drawn(e["key"])) for e in self.entries}))


class CloneValidateTest(FilesCase):
    """tools/clone_validate.py (the real-data check) understands the files strategy."""

    def test_the_tool_runs_the_full_cycle_on_a_copy_of_the_files(self):
        spec = importlib.util.spec_from_file_location(
            "clone_validate_files", os.path.join(ROOT, "tools", "clone_validate.py"))
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        lines = []
        report = tool.validate(APPID, self.install, profiles={APPID: self.profile}, log=lines.append)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(sorted(report["files"]), sorted(e["file"] for e in self.entries))
        self.assertEqual([s["style"] for s in report["steps"]], ["ps", "ps", "original"])
        self.assertTrue(any("6/6 file(s) byte-identical" in l for l in lines), lines)


class CrashTest(FilesCase):
    """A power cut at every possible moment leaves each file whole, and the next sync recovers."""

    def fresh(self):
        """A new fake game (the previous subtest's folders are removed)."""
        self.tearDown()
        self.setUp()

    def restart(self):
        """The Deck reboots: a new engine on the same folders (nothing is kept in memory)."""
        self.eng = self.new_engine()

    def assert_each_file_is_one_of_its_versions(self):
        for e in self.entries:
            self.assertIn(self.read(e["key"]), (self.original(e["key"]), self.drawn(e["key"])), e["key"])
        for key, _, _ in UNTOUCHED:
            self.assertEqual(self.read(key), self.original(key))
        self.assertEqual([], [n for _, _, ns in os.walk(self.home) for n in ns
                              if n.endswith(files_patch.TMP_SUFFIX)])

    def test_crash_while_applying_after_k_files_then_the_watcher_finishes(self):
        n = len(self.entries)
        for k in range(n):
            with self.subTest(files_written=k):
                self.fresh()
                with self.crash_after(k, self.install):
                    r = self.eng.set_style(APPID, "ps")
                self.assertFalse(r["ok"])
                self.assert_each_file_is_one_of_its_versions()
                done = [self.read(e["key"]) == self.drawn(e["key"]) for e in self.entries]
                self.assertEqual(done, [True] * k + [False] * (n - k))      # exactly the first k, in order
                self.restart()
                self.assertEqual(self.status()["state"], "error")           # the failure is visible
                results = self.eng.sync_all("watch")
                self.assertTrue(results and results[0]["ok"], results)
                self.assertEqual(self.tree(), self.patched_tree())
                self.assertEqual(self.status()["state"], "ok")

    def test_crash_while_applying_after_k_files_then_original(self):
        for k in range(len(self.entries)):
            with self.subTest(files_written=k):
                self.fresh()
                with self.crash_after(k, self.install):
                    self.eng.set_style(APPID, "ps")
                self.restart()
                self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
                self.assertEqual(self.tree(), self.pristine)
                self.assertEqual(self.status()["state"], "ok")

    def test_crash_while_restoring_after_k_files_then_the_watcher_finishes(self):
        n = len(self.entries)
        for k in range(n):
            with self.subTest(files_restored=k):
                self.fresh()
                self.eng.set_style(APPID, "ps")
                with self.crash_after(k, self.install):
                    r = self.eng.set_style(APPID, "original")
                self.assertFalse(r["ok"])
                self.assert_each_file_is_one_of_its_versions()
                restored = [self.read(e["key"]) == self.original(e["key"]) for e in self.entries]
                self.assertEqual(restored, [False] * (n - k) + [True] * k)  # undo runs in reverse order
                self.restart()
                results = self.eng.sync_all("watch")                        # the config says Original
                self.assertTrue(results and results[0]["ok"], results)
                self.assertEqual(self.tree(), self.pristine)

    def test_crash_while_restoring_after_k_files_then_ps_again(self):
        for k in range(len(self.entries)):
            with self.subTest(files_restored=k):
                self.fresh()
                self.eng.set_style(APPID, "ps")
                with self.crash_after(k, self.install):
                    self.eng.set_style(APPID, "original")
                self.restart()
                self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
                self.assertEqual(self.tree(), self.patched_tree())

    def test_crash_while_backing_up_leaves_the_game_untouched(self):
        for k in range(len(self.entries)):
            with self.subTest(backups_written=k):
                self.fresh()
                with self.crash_after(k, os.path.join(self.home, "runtime")):
                    r = self.eng.set_style(APPID, "ps")
                self.assertFalse(r["ok"])
                self.assertEqual(self.tree(), self.pristine)                # no game file was touched
                self.restart()
                self.eng.sync_all("watch")
                self.assertEqual(self.tree(), self.patched_tree())

    def test_a_temp_file_left_by_a_crash_is_cleaned_and_never_confuses_the_state(self):
        self.eng.set_style(APPID, "ps")
        stray = [self.path("Tooltip/A") + files_patch.TMP_SUFFIX,
                 os.path.join(self.backup_dir(), "Tooltip_A.orig" + files_patch.TMP_SUFFIX)]
        for path in stray:
            with open(path, "wb") as f:
                f.write(b"half a file")
        self.assertEqual(self.status()["state"], "ok")
        r = self.eng.sync(APPID)
        self.assertTrue(r["ok"] and not r["changed"])
        for path in stray:
            self.assertFalse(os.path.exists(path), path)
        self.assertEqual(self.tree(), self.patched_tree())

    def test_write_atomic_removes_its_temp_file_when_it_fails(self):
        target = self.path("Tooltip/A")
        with mock.patch.object(files_patch.os, "replace", side_effect=OSError("disk gone")):
            with self.assertRaises(OSError):
                files_patch.write_atomic(target, b"new")
        self.assertFalse(os.path.exists(target + files_patch.TMP_SUFFIX))
        self.assertEqual(self.read("Tooltip/A"), self.original("Tooltip/A"))

    def test_write_atomic_replaces_whole_or_not_at_all(self):
        target = self.path("Tooltip/A")
        files_patch.write_atomic(target, b"x" * 10)
        self.assertEqual(self.read("Tooltip/A"), b"x" * 10)
        files_patch.write_atomic(target + ".new", b"fresh")             # creates a file too
        with open(target + ".new", "rb") as f:
            self.assertEqual(f.read(), b"fresh")


if __name__ == "__main__":
    unittest.main()
