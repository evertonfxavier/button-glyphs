"""Add-file overlay strategy (overlay_patch.py + OverlayGlyphProfile): a synthetic Croteam-style game, both vehicles.

The game's own archives are plain zips (tests/overlay_fixture.py); the strategy adds `zz_ButtonGlyphs.gro` (gro vehicle)
or the same entries as loose files (loose vehicle). Every property of patch-safety-invariants is exercised on the real
Engine: check before write, exact undo, ledger before the first write, atomic writes, a cut at EVERY write boundary,
errors before pending, the running guard, unknown build, a file that is not ours, Steam verify, no stacking.
"""
import builtins
import hashlib
import os
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("py_modules", "tests", "tools"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import overlay_fixture as of  # noqa: E402
from button_glyphs import croteam_tex, engine, files_patch, gro, image, overlay_patch  # noqa: E402
from button_glyphs.profiles.overlay_glyphs import OverlayGlyphProfile, RoundSymbol  # noqa: E402,F401
from button_glyphs.profiles.region_glyphs import PictureLoader  # noqa: E402
from test_backend import GLYPH_DIR, make_library  # noqa: E402
from verify_worlds import Crash, CrashInjector  # noqa: E402

APPID = of.APPID
OUR = "zz_ButtonGlyphs.gro"


def rd(path):
    with open(path, "rb") as f:
        return f.read()


def wr(path, data):
    with open(path, "wb") as f:
        f.write(data)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tree(root):
    """{relative path: sha256 | 'DIR'} of EVERY entry under root (a stray temp file or an empty folder shows)."""
    out = {}
    for base, dirs, names in os.walk(root):
        for d in dirs:
            out[os.path.relpath(os.path.join(base, d), root)] = "DIR"
        for n in names:
            p = os.path.join(base, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = sha(f.read())
    return out


Cutter = CrashInjector        # the shared injector counts deletions too (os.remove / os.rmdir of what exists)


class OverlayCase(unittest.TestCase):
    vehicle = "gro"

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        common = make_library(self.home, [(APPID, "Serious Sam 2", of.INSTALLDIR, "4")])
        self.install = os.path.join(common, of.INSTALLDIR)
        self.art = os.path.join(self.home, "art")
        of.build_game(self.install)
        of.make_art(self.art)
        self.entries = of.entries_for(self.install)
        self.profile = of.make_profile(self.entries, self.art, vehicle=self.vehicle)
        self.pristine = tree(self.install)
        self.proc = os.path.join(self.home, "proc")
        os.makedirs(self.proc)
        self.eng = self.new_engine()

    def new_engine(self, profile=None):
        return engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={APPID: profile or self.profile}, proc_root=self.proc)

    # ---- helpers
    def status(self, eng=None):
        return next(g for g in (eng or self.eng).list_games() if g["appid"] == APPID)

    def set(self, style, eng=None):
        return (eng or self.eng).set_style(APPID, style)

    def ok(self, style, eng=None):
        res = self.set(style, eng)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["errors"], [], res)
        return res

    def backup_dir(self):
        return os.path.join(self.eng.backup_root, APPID, "overlay")

    def our_path(self):
        return os.path.join(self.install, OUR)

    def ours(self):
        """The added files that exist now, relative to the install."""
        now = tree(self.install)
        return {k: v for k, v in now.items() if k not in self.pristine}

    def expected_gro(self, style="ps", profile=None):
        profile = profile or self.profile
        originals = overlay_patch.read_originals(profile, self.install)
        return overlay_patch.targets(profile, profile.outputs(style, originals))

    def put(self, rel, data):
        path = os.path.join(self.install, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def game_files_unchanged(self):
        now = tree(self.install)
        for k, v in self.pristine.items():
            self.assertEqual(now.get(k), v, "game file changed: %s" % k)

    def choose(self, style):
        """Record the style in the config without syncing (what the UI does before the watcher runs)."""
        cfg = self.eng.load_config()
        cfg["games"][APPID] = {"style": style}
        self.eng.save_config(cfg)

    def clean_world(self):
        """Back to a fresh fake game (a new world inside the same temp folder)."""
        shutil.rmtree(self.home, ignore_errors=True)
        self.setUp()


class ApplyAndUndoTest(OverlayCase):
    def test_ps_adds_only_our_archive_and_leaves_every_game_file_byte_identical(self):
        self.ok("ps")
        self.assertEqual(list(self.ours()), [OUR])
        self.game_files_unchanged()
        st = self.status()
        self.assertEqual((st["state"], st["applied"], st["code"]), ("ok", "ps", "active"))

    def test_the_archive_holds_exactly_the_rebuilt_entries_and_they_decode_to_the_symbols(self):
        self.ok("ps")
        with open(self.our_path(), "rb") as f:
            data = f.read()
        want = dict(self.expected_gro())[OUR]
        self.assertEqual(data, want)
        with zipfile.ZipFile(self.our_path()) as z:
            self.assertEqual(z.testzip(), None)
            self.assertEqual(z.namelist(), [e["path"] for e in self.entries])
            for e in self.entries:
                new = z.read(e["path"])
                w, h = croteam_tex.parse(new).width, croteam_tex.parse(new).height
                self.assertEqual((w, h), (of.SIZE, of.SIZE))
                self.assertNotEqual(new, self.original(e))
                rgba = croteam_tex.decode(new)[0]
                box = of_box(rgba)
                # round symbol centred on the original oval's centre (10 x 12 oval -> 10 px symbol)
                self.assertEqual(box[2] - box[0], box[3] - box[1])
                self.assertLessEqual(box[2] - box[0], 10)

    def original(self, entry):
        with gro.Layers([os.path.join(self.install, n) for n in sorted(os.listdir(self.install))
                         if n.endswith(".gro") and n != OUR]) as layers:
            return layers.read(entry["path"])[1]

    def test_whole_tree_is_identical_after_ps_original_ps_original(self):
        self.ok("ps")
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)
        self.ok("ps")
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)
        self.assertEqual(self.status()["state"], "ok")

    def test_native_style_is_the_same_as_original(self):
        self.ok("ps")
        self.ok("xbox")
        self.assertEqual(tree(self.install), self.pristine)

    def test_second_apply_and_second_original_write_nothing(self):
        self.ok("ps")
        with CrashInjector([self.install, self.eng.backup_root]) as inj:
            res = self.set("ps")
        self.assertEqual(inj.count, 0)
        self.assertFalse(res["changed"])
        self.ok("original")
        with Cutter([self.install, os.path.join(self.eng.backup_root)]) as inj:
            res = self.set("original")
        self.assertEqual(inj.count, 0)
        self.assertFalse(res["changed"])

    def test_original_before_anything_is_a_no_op(self):
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)

    def test_no_game_file_is_ever_opened_for_writing(self):
        real_open, bad = builtins.open, []

        def spy(file, mode="r", *a, **kw):
            if isinstance(file, str) and any(c in mode for c in "wa+x") and file.startswith(self.install):
                if os.path.basename(file) not in (OUR, OUR + files_patch.TMP_SUFFIX):
                    bad.append(file)
            return real_open(file, mode, *a, **kw)

        with mock.patch.object(builtins, "open", spy):
            self.ok("ps")
            self.ok("original")
        self.assertEqual(bad, [])

    def test_reads_are_bounded_the_junk_entry_and_the_big_archives_are_never_read(self):
        reads = []
        real_read = zipfile.ZipFile.read

        def spy(zf, name, *a, **kw):
            data = real_read(zf, name, *a, **kw)
            reads.append((getattr(name, "filename", name), len(data)))
            return data

        with mock.patch.object(zipfile.ZipFile, "read", spy):
            self.ok("ps")
            self.status()
        names = {n for n, _ in reads}
        self.assertFalse([n for n in names if "junk" in n], names)
        self.assertLessEqual(max(sz for _, sz in reads), max(e["size"] for e in self.entries) + 20000)

    def test_the_ledger_is_on_disk_before_the_first_write_to_the_game_folder(self):
        seen = []
        real_replace = os.replace

        def spy(src, dst, *a, **kw):
            if dst.startswith(self.install):
                seen.append(overlay_patch.files_patch.read_ledger(self.backup_dir()).copy())
            return real_replace(src, dst, *a, **kw)

        with mock.patch.object(os, "replace", spy):
            self.ok("ps")
        want = sha(dict(self.expected_gro())[OUR])
        self.assertTrue(seen and all(want in k for k in seen), seen)

    def test_temp_file_is_not_left_and_the_permissions_are_sane(self):
        self.ok("ps")
        self.assertEqual([n for n in os.listdir(self.install) if n.endswith(".tmp")], [])
        self.assertTrue(os.access(self.our_path(), os.R_OK))

    def test_apply_is_deterministic(self):
        self.ok("ps")
        first = rd(self.our_path())
        self.ok("original")
        self.ok("ps")
        self.assertEqual(rd(self.our_path()), first)


def of_box(rgba, w=of.SIZE, h=of.SIZE):
    from button_glyphs.profiles.overlay_glyphs import opaque_box
    return opaque_box(rgba, w, h)


class CheapStatusTest(OverlayCase):
    """The status poll and the idle watcher tick answer from cheap facts: nothing is built, inflated or hashed twice."""

    def boom(self, *a, **kw):
        raise AssertionError("the cheap path built or read the game")

    def test_status_and_an_idle_tick_build_and_read_nothing(self):
        self.ok("ps")
        with mock.patch.object(overlay_patch, "read_originals", self.boom), \
                mock.patch.object(OverlayGlyphProfile, "outputs", self.boom), \
                mock.patch.object(zipfile.ZipFile, "read", self.boom):
            st = self.status()
            self.assertEqual((st["state"], st["code"]), ("ok", "active"))
            with Cutter([self.install, self.eng.backup_root]) as inj:
                self.assertEqual([r["changed"] for r in self.eng.sync_all("watch")], [False])
            self.assertEqual(inj.count, 0)

    def test_the_archive_directories_are_parsed_once_and_again_after_a_change(self):
        self.ok("ps")
        parsed = []
        real = zipfile.ZipFile

        def spy(path, *a, **kw):
            parsed.append(os.path.basename(str(path)))
            return real(path, *a, **kw)

        with mock.patch.object(zipfile, "ZipFile", spy):
            self.status()
            self.status()
        self.assertEqual(parsed, [])                       # already parsed by the apply's own plan
        overlay_patch.clear_caches()
        with mock.patch.object(zipfile, "ZipFile", spy):
            self.status()
            first = len(parsed)
            self.status()
        self.assertEqual(first, 3)                         # the three game archives, once
        self.assertEqual(len(parsed), 3)
        # an archive that changes (size) is parsed again without anyone clearing the cache
        extra = os.path.join(self.install, "Movies_PC_01.gro")
        wr(extra, gro.build([("Content/Movies/b.bin", b"x" * 5000)]))
        with mock.patch.object(zipfile, "ZipFile", spy):
            self.status()
        self.assertEqual(parsed[3:], ["Movies_PC_01.gro"])

    def test_a_pin_with_the_right_size_but_another_crc_is_an_unknown_build_without_reading_any_entry(self):
        entries = [dict(self.entries[0], crc32="deadbeef")] + self.entries[1:]
        eng = self.new_engine(of.make_profile(entries, self.art))
        self.choose("ps")
        with mock.patch.object(zipfile.ZipFile, "read", self.boom):
            st = self.status(eng)
        self.assertEqual((st["state"], st["code"]), ("error", "unsupported_build"))

    def test_a_changed_bundled_picture_makes_the_file_stale_without_building(self):
        self.ok("ps")
        of.make_art(self.art, shift=1)
        fresh = of.make_profile(self.entries, self.art)
        eng = self.new_engine(fresh)
        with mock.patch.object(OverlayGlyphProfile, "outputs", self.boom):
            st = self.status(eng)
        self.assertEqual((st["state"], st["code"]), ("pending", "will_improve"))

    def test_a_changed_drawing_code_makes_the_file_stale(self):
        self.ok("ps")
        self.choose("ps")
        with mock.patch("button_glyphs.profiles.overlay_glyphs._source_digest", return_value=b"x" * 32):
            self.assertEqual(self.status()["code"], "will_improve")
        self.assertEqual(self.status()["code"], "active")

    def test_a_lost_build_record_is_stale_then_healed_without_rewriting_the_file(self):
        self.ok("ps")
        os.remove(os.path.join(self.backup_dir(), overlay_patch.BUILDS))
        self.assertEqual(self.status()["code"], "will_improve")
        before = os.stat(self.our_path())
        res = self.eng.sync(APPID, reason="watch")
        self.assertTrue(res["ok"], res)
        after = os.stat(self.our_path())
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(self.status()["code"], "active")

    def test_a_damaged_build_record_line_is_ignored(self):
        self.ok("ps")
        path = os.path.join(self.backup_dir(), overlay_patch.BUILDS)
        wr(path, rd(path)[:-10])                           # torn last line
        self.assertEqual(self.status()["code"], "will_improve")
        self.assertTrue(self.eng.sync(APPID, reason="watch")["ok"])
        self.assertEqual(self.status()["code"], "active")

    def test_the_build_record_is_on_disk_before_the_first_write_to_the_game_folder(self):
        seen = []
        real_replace = os.replace

        def spy(src, dst, *a, **kw):
            if dst.startswith(self.install):
                seen.append(len(overlay_patch.read_builds(self.backup_dir())))
            return real_replace(src, dst, *a, **kw)

        with mock.patch.object(os, "replace", spy):
            self.ok("ps")
        self.assertTrue(seen and all(n >= 1 for n in seen), seen)

    def test_a_failed_build_is_not_retried_by_the_watcher_every_minute_but_a_user_choice_retries_at_once(self):
        calls = []
        real = OverlayGlyphProfile.outputs

        def failing(profile, style, originals):
            calls.append(1)
            raise RuntimeError("encoder exploded")

        self.choose("ps")
        with mock.patch.object(OverlayGlyphProfile, "outputs", failing):
            self.assertFalse(self.eng.sync(APPID, reason="watch")["ok"])
            n = len(calls)
            self.assertGreaterEqual(n, 1)
            self.assertFalse(self.eng.sync(APPID, reason="watch")["ok"])
            self.assertEqual(len(calls), n)                # the second tick did not build again
            self.assertEqual(self.status()["state"], "error")
            self.assertFalse(self.set("ps")["ok"])
            self.assertGreater(len(calls), n)              # an explicit choice does
        self.ok("ps")                                      # and a good one clears the wait
        self.assertEqual(self.status()["state"], "ok")
        self.assertNotIn("overlay_retry_after", self.eng.load_config()["games"][APPID])

    def test_a_foreign_file_is_adopted_check_runs_once_for_the_watcher(self):
        self.put(OUR, b"not ours")
        self.choose("ps")
        calls = []
        real = overlay_patch.build_plan

        def spy(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        with mock.patch.object(overlay_patch, "build_plan", spy):
            self.assertFalse(self.eng.sync(APPID, reason="watch")["ok"])
            n = len(calls)
            self.assertFalse(self.eng.sync(APPID, reason="watch")["ok"])
        self.assertEqual(len(calls), n)
        self.assertEqual(rd(self.our_path()), b"not ours")


class StateTest(OverlayCase):
    def test_steam_verify_deleting_our_file_is_original_again_and_is_reapplied(self):
        self.ok("ps")
        os.remove(self.our_path())
        st = self.status()
        self.assertEqual((st["state"], st["code"]), ("pending", "will_apply"))
        res = self.eng.sync(APPID, reason="watch")
        self.assertTrue(res["ok"] and res["changed"], res)
        self.assertEqual(self.status()["state"], "ok")
        self.assertEqual(list(self.ours()), [OUR])

    def test_the_game_settings_lost_does_not_matter(self):
        self.ok("ps")
        os.remove(self.eng.config_path)
        self.assertEqual(self.status()["style"], "original")
        self.assertEqual(self.status()["state"], "pending")          # we are set to Original and our file is there
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)

    def test_an_older_drawing_of_ours_is_stale_and_rebuilt_from_the_originals(self):
        self.ok("ps")
        old = rd(self.our_path())
        of.make_art(self.art, shift=1)                                 # new art, new revision
        new_profile = of.make_profile(self.entries, self.art, revision=2)
        eng = self.new_engine(new_profile)
        st = self.status(eng)
        self.assertEqual((st["state"], st["code"]), ("pending", "will_improve"))
        self.ok("ps", eng)
        now = rd(self.our_path())
        self.assertNotEqual(now, old)
        self.assertEqual(now, dict(self.expected_gro(profile=new_profile))[OUR])      # not stacked on the old file
        self.game_files_unchanged()
        self.assertEqual(self.status(eng)["state"], "ok")

    def test_original_removes_a_stale_file_of_ours(self):
        self.ok("ps")
        of.make_art(self.art, shift=1)
        eng = self.new_engine(of.make_profile(self.entries, self.art, revision=2))
        self.ok("original", eng)
        self.assertEqual(tree(self.install), self.pristine)

    def test_a_file_with_our_name_that_is_not_ours_is_refused_and_never_touched(self):
        self.put(OUR, b"somebody else's archive")
        before = tree(self.install)
        self.choose("ps")
        st = self.status()
        self.assertEqual((st["state"], st["code"], st["args"]), ("error", "foreign_file", {"file": OUR}))
        res = self.set("ps")
        self.assertFalse(res["ok"])
        self.assertEqual(tree(self.install), before)
        self.assertFalse(os.path.exists(self.backup_dir()) and os.listdir(self.backup_dir()))   # no ledger either

    def test_original_leaves_a_foreign_file_alone_and_says_so(self):
        self.put(OUR, b"somebody else's archive")
        before = tree(self.install)
        self.ok("original")
        self.assertEqual(tree(self.install), before)
        st = self.status()
        self.assertEqual((st["state"], st["code"]), ("ok", "foreign_file"))

    def test_a_directory_or_a_symlink_with_our_name_is_foreign_not_read(self):
        os.mkdir(self.our_path())
        self.assertFalse(self.set("ps")["ok"])
        os.rmdir(self.our_path())
        os.symlink(os.path.join(self.install, "Sam2.exe"), self.our_path())
        self.assertFalse(self.set("ps")["ok"])
        self.assertTrue(os.path.islink(self.our_path()))

    def test_a_file_of_ours_whose_ledger_was_lost_is_adopted_by_the_next_sync_when_it_equals_todays_output(self):
        self.ok("ps")
        shutil.rmtree(self.backup_dir())
        self.assertEqual(self.status()["code"], "foreign_file")        # cheap facts cannot prove it: unknown for now
        res = self.eng.sync(APPID, reason="watch")                     # the full build proves it is today's output
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.status()["state"], "ok")
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)

    def test_a_lost_ledger_under_original_is_still_recognised_and_removed(self):
        self.ok("ps")
        shutil.rmtree(self.backup_dir())
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)

    def test_an_old_file_of_ours_whose_ledger_was_lost_is_foreign(self):
        self.ok("ps")
        of.make_art(self.art, shift=1)
        eng = self.new_engine(of.make_profile(self.entries, self.art, revision=2))
        shutil.rmtree(self.backup_dir())
        before = tree(self.install)
        self.assertFalse(self.set("ps", eng)["ok"])
        self.assertEqual(tree(self.install), before)
        self.ok("original", eng)                                       # left alone, reported
        self.assertEqual(tree(self.install), before)
        self.assertEqual(self.status(eng)["code"], "foreign_file")

    def test_a_ledger_hash_on_a_torn_line_does_not_make_a_file_ours(self):
        self.ok("ps")
        of.make_art(self.art, shift=1)
        eng = self.new_engine(of.make_profile(self.entries, self.art, revision=2))
        path = files_patch.ledger_path(self.backup_dir())
        line = rd(path)
        wr(path, line.rstrip(b"\n"))                     # torn: no newline
        self.assertFalse(self.set("ps", eng)["ok"])

    def test_our_file_is_never_read_as_an_original(self):
        """Reading the originals skips our own archive: a drawn file can never be taken for the game's."""
        self.ok("ps")
        paths = overlay_patch.archives(self.profile, self.install)
        self.assertNotIn(OUR, [os.path.basename(p) for p in paths])
        overlay_patch.read_originals(self.profile, self.install)       # still the pinned originals

    def test_a_vehicle_that_the_other_one_left_behind_is_removed_when_ours(self):
        self.ok("ps")
        loose = of.make_profile(self.entries, self.art, vehicle="loose")
        eng = self.new_engine(loose)
        self.assertEqual(self.status(eng)["state"], "pending")
        self.ok("ps", eng)
        now = self.ours()
        self.assertNotIn(OUR, now)
        self.assertEqual(sorted(k for k, v in now.items() if v != "DIR"), sorted(e["path"] for e in self.entries))
        self.ok("original", eng)
        self.assertEqual(tree(self.install), self.pristine)


class RefusalTest(OverlayCase):
    def refuse(self, expect_code="unsupported_build"):
        before = tree(self.install)
        cfg = self.eng.load_config()
        cfg["games"][APPID] = {"style": "ps"}
        self.eng.save_config(cfg)
        st = self.status()                       # the status alone (no failed sync yet) already says why
        self.assertEqual((st["state"], st.get("code")), ("error", expect_code), st)
        res = self.set("ps")
        self.assertFalse(res["ok"], res)
        self.assertEqual(tree(self.install), before)
        self.assertFalse(os.path.exists(self.backup_dir()) and os.listdir(self.backup_dir()), "ledger written")
        self.assertEqual(self.status()["state"], "error")
        return st

    def test_a_game_update_that_changes_an_original_entry_is_an_unknown_build(self):
        of.build_game(self.install, variant=1)
        self.pristine = tree(self.install)
        st = self.refuse()
        self.assertIn("not the supported build", st["message"])

    def test_an_extra_archive_that_overrides_an_entry_is_an_unknown_build(self):
        k = self.entries[0]
        of.build_game(self.install, extra_archives=[("Patch_02_110.gro", [(k["path"], of.texture("A_button", 16, 9))])])
        self.pristine = tree(self.install)
        self.refuse()

    def test_an_extra_archive_with_unrelated_entries_is_fine(self):
        of.build_game(self.install, extra_archives=[("Patch_02_110.gro", [("Content/x.bin", b"1234")])])
        self.pristine = tree(self.install)
        self.ok("ps")
        self.game_files_unchanged()

    def test_an_entry_won_by_another_archive_is_refused(self):
        entries = [dict(e, gro="All_PC_01.gro") for e in self.entries]
        eng = self.new_engine(of.make_profile(entries, self.art))
        before = tree(self.install)
        self.assertFalse(self.set("ps", eng)["ok"])
        self.assertEqual(tree(self.install), before)

    def test_a_missing_entry_is_refused(self):
        entries = [dict(e, path=e["path"].replace("A_button", "Z_button")) if i == 0 else e
                   for i, e in enumerate(self.entries)]
        eng = self.new_engine(of.make_profile(entries, self.art))
        self.assertFalse(self.set("ps", eng)["ok"])
        self.assertEqual(tree(self.install), self.pristine)

    def test_a_loose_file_that_would_shadow_an_entry_is_foreign(self):
        self.put(self.entries[0]["path"], b"someone's loose texture")
        before = tree(self.install)
        self.choose("ps")
        self.assertEqual(self.status()["code"], "foreign_file")
        self.assertFalse(self.set("ps")["ok"])
        self.assertEqual(tree(self.install), before)

    def test_a_damaged_archive_in_the_folder_is_a_refusal_not_a_crash(self):
        with open(os.path.join(self.install, "Movies_PC_01.gro"), "wb") as f:
            f.write(b"PK\x03\x04 truncated")
        before = tree(self.install)
        self.choose("ps")
        self.assertEqual(self.status()["code"], "cannot_read")
        self.assertFalse(self.set("ps")["ok"])
        self.assertEqual(tree(self.install), before)

    def test_no_archive_at_all_is_refused(self):
        for n in os.listdir(self.install):
            if n.endswith(".gro"):
                os.remove(os.path.join(self.install, n))
        self.pristine = tree(self.install)
        self.refuse()

    def test_an_entry_bigger_than_its_pin_is_refused_without_reading_it(self):
        entries = [dict(e, size=e["size"] - 1) for e in self.entries]
        eng = self.new_engine(of.make_profile(entries, self.art))
        self.assertFalse(self.set("ps", eng)["ok"])
        self.assertEqual(tree(self.install), self.pristine)

    def test_original_after_an_update_still_removes_ours_without_reading_the_game(self):
        self.ok("ps")
        of.build_game(self.install, variant=1)                          # the update replaced the originals (and kept our file)
        with mock.patch.object(overlay_patch, "read_originals", side_effect=AssertionError("read the game")):
            self.ok("original")
        self.assertFalse(os.path.exists(self.our_path()))

    def test_not_enough_free_space_writes_nothing(self):
        before = tree(self.install)
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=1000)):
            res = self.set("ps")
        self.assertFalse(res["ok"])
        self.assertEqual(tree(self.install), before)

    def test_a_write_that_does_not_hold_is_an_error(self):
        real = files_patch.write_atomic

        def torn(path, data):
            real(path, data[:-1] if path.endswith(OUR) else data)

        with mock.patch.object(overlay_patch.files_patch, "write_atomic", torn):
            res = self.set("ps")
        self.assertFalse(res["ok"])
        self.assertIn("does not hold", res["errors"][0])

    def test_a_pin_with_the_right_size_but_another_hash_is_refused(self):
        entries = [dict(self.entries[0], sha256="0" * 64)] + self.entries[1:]
        eng = self.new_engine(of.make_profile(entries, self.art))
        self.assertFalse(self.set("ps", eng)["ok"])
        self.assertEqual(tree(self.install), self.pristine)

    def test_an_archive_that_does_not_read_back_is_never_written(self):
        real = gro.build

        def bad(entries, deflate=False):
            out = real(entries, deflate)
            return out[:100] + bytes([out[100] ^ 1]) + out[101:]          # one flipped byte inside the first entry

        with mock.patch.object(overlay_patch.gro, "build", bad):
            res = self.set("ps")
        self.assertFalse(res["ok"])
        self.assertEqual(tree(self.install), self.pristine)

    def test_a_file_that_changed_after_the_plan_is_not_deleted(self):
        self.ok("ps")
        plan = overlay_patch.plan(self.profile, self.install, self.backup_dir(), "original")
        wr(self.our_path(), b"the user replaced it meanwhile")
        with self.assertRaises(RuntimeError):
            overlay_patch.revert(self.profile, self.install, self.backup_dir(), plan)
        self.assertEqual(rd(self.our_path()), b"the user replaced it meanwhile")

    def test_art_changed_on_disk_after_a_status_poll_is_what_the_apply_draws(self):
        self.choose("ps")
        self.status()                                         # a poll for PlayStation memoises today's outputs
        of.make_art(self.art, shift=2)                        # the art changed (same revision: a dev edit)
        self.ok("ps")
        fresh = of.make_profile(self.entries, self.art)
        self.assertEqual(rd(self.our_path()), dict(self.expected_gro(profile=fresh))[OUR])

    def test_a_temp_file_of_ours_is_removed_even_when_there_is_nothing_to_do(self):
        tmp = self.our_path() + files_patch.TMP_SUFFIX
        wr(tmp, b"half")
        self.ok("original")
        self.assertFalse(os.path.exists(tmp))


class PreflightTest(OverlayCase):
    def test_a_missing_picture_fails_before_anything_is_built_or_written(self):
        os.remove(os.path.join(self.art, "glyphs", "ps", "b-icon.png"))
        before = tree(self.install)
        res = self.set("ps")
        self.assertFalse(res["ok"])
        self.assertIn("b-icon", res["errors"][0])
        self.assertEqual(tree(self.install), before)
        self.assertFalse(os.path.exists(self.backup_dir()) and os.listdir(self.backup_dir()))

    def test_a_truncated_picture_fails_the_same_way(self):
        p = os.path.join(self.art, "glyphs", "ps", "a-icon.png")
        data = rd(p)
        wr(p, data[:40])
        self.assertFalse(self.set("ps")["ok"])
        self.assertEqual(tree(self.install), self.pristine)

    def test_an_empty_picture_is_refused(self):
        image.write_png(os.path.join(self.art, "glyphs", "ps", "a-icon.png"), bytes(12 * 12 * 4), 12, 12)
        self.assertFalse(self.set("ps")["ok"])
        self.assertEqual(tree(self.install), self.pristine)

    def test_original_still_works_with_the_art_gone(self):
        self.ok("ps")
        shutil.rmtree(self.art)
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)

    def test_ps_with_the_art_gone_after_a_patch_is_an_error_not_a_loop(self):
        self.ok("ps")
        shutil.rmtree(self.art)
        res = self.eng.sync(APPID, reason="watch")
        self.assertFalse(res["ok"])
        self.assertEqual(self.status()["state"], "error")
        self.assertEqual(list(self.ours()), [OUR])                     # what is there is untouched


class GuardsAndStatusTest(OverlayCase):
    def make_running(self):
        os.makedirs(os.path.join(self.proc, "4242"))
        with open(os.path.join(self.proc, "4242", "cmdline"), "wb") as f:
            f.write(self.install.encode() + b"/Sam2.exe\0")

    def test_nothing_is_written_while_the_game_runs_and_the_watcher_retries(self):
        self.make_running()
        with Cutter([self.install, self.eng.backup_root]) as inj:
            res = self.set("ps")
        self.assertTrue(res["ok"])
        self.assertEqual(self.ours(), {})
        self.assertEqual(self.status()["state"], "running")
        shutil.rmtree(os.path.join(self.proc, "4242"))
        self.eng.sync_all("watch")
        self.assertEqual(list(self.ours()), [OUR])

    def test_original_chosen_while_the_game_runs_is_finished_by_the_watcher(self):
        self.ok("ps")
        self.make_running()
        self.set("original")
        self.assertEqual(list(self.ours()), [OUR])
        shutil.rmtree(os.path.join(self.proc, "4242"))
        self.eng.sync_all("watch")
        self.assertEqual(tree(self.install), self.pristine)

    def test_nothing_is_written_while_steam_updates_the_game(self):
        make_library(self.home, [(APPID, "Serious Sam 2", of.INSTALLDIR, "1030")])
        self.set("ps")
        self.assertEqual(self.ours(), {})
        self.assertEqual(self.status()["state"], "updating")

    def test_status_order_running_updating_error_refusal_pending_ok(self):
        self.assertEqual(self.status()["state"], "ok")                 # Original, nothing to do
        cfg = self.eng.load_config()
        cfg["games"][APPID] = {"style": "ps"}
        self.eng.save_config(cfg)
        self.assertEqual((self.status()["state"], self.status()["code"]), ("pending", "will_apply"))
        cfg["games"][APPID]["error"] = "boom"
        self.eng.save_config(cfg)
        self.assertEqual((self.status()["state"], self.status()["message"]), ("error", "boom"))   # error before pending
        self.make_running()
        self.assertEqual(self.status()["state"], "running")            # running outranks the error
        shutil.rmtree(os.path.join(self.proc, "4242"))
        make_library(self.home, [(APPID, "Serious Sam 2", of.INSTALLDIR, "1030")])
        self.assertEqual(self.status()["state"], "updating")

    def test_an_unsupported_build_outranks_pending(self):
        of.build_game(self.install, variant=1)
        cfg = self.eng.load_config()
        cfg["games"][APPID] = {"style": "ps"}
        self.eng.save_config(cfg)
        st = self.status()
        self.assertEqual((st["state"], st["code"]), ("error", "unsupported_build"))

    def test_a_foreign_file_outranks_pending(self):
        self.put(OUR, b"x")
        cfg = self.eng.load_config()
        cfg["games"][APPID] = {"style": "ps"}
        self.eng.save_config(cfg)
        self.assertEqual(self.status()["state"], "error")

    def test_a_failed_sync_shows_its_error_until_the_next_good_one(self):
        of.build_game(self.install, variant=1)
        self.set("ps")
        self.assertEqual(self.status()["state"], "error")
        of.build_game(self.install, variant=0)
        self.ok("ps")
        self.assertEqual(self.status()["state"], "ok")

    def test_a_plan_that_raises_is_an_error_for_this_game_only(self):
        with mock.patch.object(overlay_patch, "plan", side_effect=OSError("disk gone")):
            st = self.status()
        self.assertEqual((st["state"], st["code"]), ("error", "cannot_read"))

    def test_revision_and_roles_reach_the_ui(self):
        self.assertEqual(self.status()["roles"], ["A"])

    def test_will_restore_is_pending_under_original(self):
        self.ok("ps")
        cfg = self.eng.load_config()
        cfg["games"][APPID]["style"] = "original"
        self.eng.save_config(cfg)
        st = self.status()
        self.assertEqual((st["state"], st["code"]), ("pending", "will_restore"))


class WatcherTest(OverlayCase):
    def test_an_idle_tick_writes_nothing(self):
        self.ok("ps")
        self.ok("original")
        before = tree(self.install)
        mtime = os.stat(self.eng.config_path).st_mtime_ns
        with Cutter([self.install, self.eng.backup_root]) as inj:
            self.assertEqual(self.eng.sync_all("watch"), [])
        self.assertEqual(inj.count, 0)
        self.assertEqual(tree(self.install), before)
        self.assertEqual(os.stat(self.eng.config_path).st_mtime_ns, mtime)

    def test_a_file_that_is_not_ours_never_triggers_a_sync(self):
        self.ok("ps")
        self.ok("original")
        self.put(OUR, b"not ours")
        with mock.patch.object(engine.Engine, "sync", side_effect=AssertionError("synced")):
            self.assertEqual(self.eng.sync_all("watch"), [])
        self.assertEqual(rd(self.our_path()), b"not ours")

    def test_a_style_game_is_synced_as_before(self):
        self.ok("ps")
        self.assertEqual([r["changed"] for r in self.eng.sync_all("watch")], [False])

    def test_an_updating_game_is_left_alone_by_the_restore(self):
        self.ok("ps")
        cfg = self.eng.load_config()
        cfg["games"][APPID]["style"] = "original"
        self.eng.save_config(cfg)
        make_library(self.home, [(APPID, "Serious Sam 2", of.INSTALLDIR, "1030")])
        self.eng.sync_all("watch")
        self.assertEqual(list(self.ours()), [OUR])
        make_library(self.home, [(APPID, "Serious Sam 2", of.INSTALLDIR, "4")])
        self.eng.sync_all("watch")
        self.assertEqual(tree(self.install), self.pristine)

    def test_the_watcher_goes_on_when_one_game_cannot_be_read(self):
        self.ok("ps")
        cfg = self.eng.load_config()
        cfg["games"][APPID]["style"] = "original"
        self.eng.save_config(cfg)
        with mock.patch.object(overlay_patch, "plan", side_effect=OSError("no")):
            self.assertEqual(self.eng.sync_all("watch"), [])


class CrashTest(OverlayCase):
    """A power cut at EVERY write event (open/write, os.replace, os.remove, os.rmdir) of apply and of Original."""

    def count(self, action):
        with Cutter([self.install, self.eng.backup_root]) as inj:
            action()
        return inj.count

    def cut(self, k, action):
        try:
            with Cutter([self.install, self.eng.backup_root], k=k):
                action()
        except Crash:
            pass
        cfg = self.eng.load_config() if os.path.exists(self.eng.config_path) else None
        if cfg and APPID in cfg["games"]:
            cfg["games"][APPID].pop("error", None)
            cfg["games"][APPID].pop("overlay_retry_after", None)       # a real power cut records nothing
            self.eng.save_config(cfg)

    def whole_files_only(self):
        """Every file that exists is a complete version: the original game file, or a complete file of ours."""
        now = tree(self.install)
        for k, v in self.pristine.items():
            self.assertEqual(now.get(k), v, "game file damaged: %s" % k)
        wanted = {sha(d) for _, d in self.expected_gro()}
        for k, v in now.items():
            if k not in self.pristine and v != "DIR" and not k.endswith(files_patch.TMP_SUFFIX):
                self.assertIn(v, wanted, "a half-written file of ours: %s" % k)

    def test_apply_cut_at_every_event_then_the_next_sync_finishes(self):
        n = self.count(lambda: self.set("ps"))
        self.assertGreaterEqual(n, 4)
        self.clean_world()
        for k in range(n):
            self.clean_world()
            self.cut(k, lambda: self.set("ps"))
            self.whole_files_only()
            res = self.eng.sync(APPID, reason="watch")
            self.assertTrue(res["ok"], (k, res))
            self.assertEqual(self.status()["state"], "ok", k)
            self.assertEqual(sorted(k2 for k2, v in self.ours().items() if v != "DIR" and not k2.endswith(".tmp")),
                             sorted(r for r, _ in self.expected_gro()), k)
            self.ok("original")
            self.assertEqual(tree(self.install), self.pristine, k)

    def test_apply_cut_at_every_event_then_original_restores(self):
        n = self.count(lambda: self.set("ps"))
        for k in range(n):
            self.clean_world()
            self.cut(k, lambda: self.set("ps"))
            self.whole_files_only()
            self.ok("original")
            self.assertEqual(tree(self.install), self.pristine, k)

    def test_nothing_of_the_game_folder_changes_before_the_ledger_is_durable(self):
        n = self.count(lambda: self.set("ps"))
        for k in range(n):
            self.clean_world()
            self.cut(k, lambda: self.set("ps"))
            if self.ours():
                ledger = files_patch.read_ledger(self.backup_dir())
                for rel, v in self.ours().items():
                    if v != "DIR" and not rel.endswith(".tmp"):
                        self.assertIn(v, ledger, "a file was written before its hash was in the ledger (k=%d)" % k)

    def test_original_cut_at_every_event_then_the_watcher_finishes(self):
        self.ok("ps")
        n = self.count(lambda: self.set("original"))
        self.assertGreaterEqual(n, 1)
        for k in range(n):
            self.clean_world()
            self.ok("ps")
            self.cut(k, lambda: self.set("original"))
            self.whole_files_only()
            results = self.eng.sync_all("watch")
            self.assertEqual(tree(self.install), self.pristine, k)
            self.assertEqual(self.status()["state"], "ok", k)
            self.assertEqual(self.eng.sync_all("watch"), [], k)         # then quiet

    def test_a_temp_file_left_by_a_crash_is_cleaned_and_never_confuses_the_state(self):
        tmp = self.our_path() + files_patch.TMP_SUFFIX
        wr(tmp, b"half")
        led = files_patch.ledger_path(self.backup_dir()) + files_patch.TMP_SUFFIX
        os.makedirs(self.backup_dir())
        wr(led, b"half")
        self.assertEqual(self.status()["state"], "ok")
        self.ok("ps")
        self.assertFalse(os.path.exists(tmp) or os.path.exists(led))
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)


class LooseVehicleTest(OverlayCase):
    vehicle = "loose"

    def written(self):
        return sorted(k for k, v in self.ours().items() if v != "DIR")

    def test_ps_writes_the_entries_as_loose_files_and_original_removes_files_and_folders(self):
        self.ok("ps")
        self.assertEqual(self.written(), sorted(e["path"] for e in self.entries))
        self.assertNotIn(OUR, self.ours())
        self.game_files_unchanged()
        for e in self.entries:
            with open(os.path.join(self.install, *e["path"].split("/")), "rb") as f:
                new = f.read()
            self.assertEqual(croteam_tex.parse(new).tobytes(), new)
            self.assertNotEqual(new, self.original_of(e))
        self.assertEqual(self.status()["state"], "ok")
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)       # the folders we made are gone too
        self.assertEqual(overlay_patch.read_journal(self.backup_dir()), [])

    def original_of(self, entry):
        return overlay_patch.read_originals(self.profile, self.install)[entry["path"]]

    def test_a_folder_that_existed_before_is_kept(self):
        os.makedirs(os.path.join(self.install, "Content", "SeriousSam2", "Textures"))
        self.pristine = tree(self.install)
        self.ok("ps")
        self.ok("original")
        self.assertEqual(tree(self.install), self.pristine)

    def test_a_folder_that_got_a_foreign_file_is_kept(self):
        self.ok("ps")
        keep = os.path.join(self.install, "Content", "SeriousSam2", "Textures", "Menu", "mine.txt")
        wr(keep, b"mine")
        self.ok("original")
        self.assertTrue(os.path.exists(keep))
        self.assertEqual(self.written(), ["Content/SeriousSam2/Textures/Menu/mine.txt"])

    def test_a_loose_file_that_is_not_ours_is_refused_and_kept(self):
        self.put(self.entries[1]["path"], b"someone's loose texture")
        before = tree(self.install)
        self.assertFalse(self.set("ps")["ok"])
        self.assertEqual(tree(self.install), before)
        self.ok("original")
        self.assertEqual(tree(self.install), before)

    def test_a_half_applied_tree_is_completed_and_a_deleted_file_comes_back(self):
        self.ok("ps")
        victim = os.path.join(self.install, *self.entries[0]["path"].split("/"))
        os.remove(victim)
        st = self.status()
        self.assertEqual((st["state"], st["code"]), ("pending", "will_apply"))
        self.eng.sync(APPID, reason="watch")
        self.assertTrue(os.path.exists(victim))
        self.assertEqual(self.status()["state"], "ok")

    def test_a_changed_loose_file_of_ours_is_stale_when_its_hash_is_in_the_ledger(self):
        self.ok("ps")
        of.make_art(self.art, shift=1)
        eng = self.new_engine(of.make_profile(self.entries, self.art, vehicle="loose", revision=2))
        self.assertEqual(self.status(eng)["code"], "will_improve")
        self.ok("ps", eng)
        self.assertEqual(self.status(eng)["state"], "ok")

    def count(self, action):
        with Cutter([self.install, self.eng.backup_root]) as inj:
            action()
        return inj.count

    def cut(self, k, action):
        try:
            with Cutter([self.install, self.eng.backup_root], k=k):
                action()
        except Crash:
            pass
        cfg = self.eng.load_config() if os.path.exists(self.eng.config_path) else None
        if cfg and APPID in cfg["games"]:
            cfg["games"][APPID].pop("error", None)
            cfg["games"][APPID].pop("overlay_retry_after", None)
            self.eng.save_config(cfg)

    def test_apply_cut_at_every_event_then_finish_or_undo(self):
        n = self.count(lambda: self.set("ps"))
        self.assertGreater(n, 8)
        for k in range(n):
            self.clean_world()
            self.cut(k, lambda: self.set("ps"))
            now = tree(self.install)
            for rel, v in self.pristine.items():
                self.assertEqual(now.get(rel), v, k)
            res = self.eng.sync(APPID, reason="watch")
            self.assertTrue(res["ok"], (k, res))
            self.assertEqual(self.status()["state"], "ok", k)
            self.ok("original")
            self.assertEqual(tree(self.install), self.pristine, k)
        for k in range(n):
            self.clean_world()
            self.cut(k, lambda: self.set("ps"))
            self.ok("original")
            self.assertEqual(tree(self.install), self.pristine, k)

    def test_original_cut_at_every_event_then_the_watcher_finishes(self):
        self.ok("ps")
        n = self.count(lambda: self.set("original"))
        self.assertGreater(n, 4)
        for k in range(n):
            self.clean_world()
            self.ok("ps")
            self.cut(k, lambda: self.set("original"))
            self.eng.sync_all("watch")
            self.assertEqual(tree(self.install), self.pristine, k)
            self.assertEqual(self.status()["state"], "ok", k)

    def test_the_journal_is_written_before_a_folder_is_made(self):
        seen = []
        real = os.makedirs

        def spy(path, *a, **kw):
            if str(path).startswith(self.install):
                seen.append(overlay_patch.read_journal(self.backup_dir()))
            return real(path, *a, **kw)

        with mock.patch.object(os, "makedirs", spy):
            self.ok("ps")
        self.assertTrue(seen and all("Content/SeriousSam2/Textures" in j for j in seen), seen)

    def test_switching_from_loose_to_gro_removes_the_loose_files_and_the_folders_we_made(self):
        self.ok("ps")
        gro_profile = of.make_profile(self.entries, self.art, vehicle="gro")
        eng = self.new_engine(gro_profile)
        self.ok("ps", eng)
        self.assertEqual(list(self.ours()), [OUR])               # no loose file, no folder left behind
        self.ok("original", eng)
        self.assertEqual(tree(self.install), self.pristine)

    def test_a_folder_that_is_empty_but_was_not_made_by_us_is_not_removed_by_original(self):
        self.ok("ps")
        extra = os.path.join(self.install, "Content", "SeriousSam2", "Textures", "Menu", "Mine")
        os.makedirs(extra)
        self.ok("original")
        self.assertTrue(os.path.isdir(extra))                    # not journaled: kept (its parents stay too)

    def test_the_apply_cut_at_every_event_keeps_the_journal_honest(self):
        n = self.count(lambda: self.set("ps"))
        for k in range(n):
            self.clean_world()
            self.cut(k, lambda: self.set("ps"))
            for d in overlay_patch.read_journal(self.backup_dir()):
                self.assertNotIn(d, self.pristine)               # only folders that did not exist before

    def test_the_watcher_reacts_to_our_loose_files_only(self):
        self.ok("ps")
        cfg = self.eng.load_config()
        cfg["games"][APPID]["style"] = "original"
        self.eng.save_config(cfg)
        self.assertEqual(len(self.eng.sync_all("watch")), 1)
        self.assertEqual(tree(self.install), self.pristine)
        self.assertEqual(self.eng.sync_all("watch"), [])


CHILD = r"""
import json, os, sys
root, mode, k = sys.argv[1], sys.argv[2], int(sys.argv[3])
for sub in ("py_modules", "tests", "tools"):
    sys.path.insert(0, os.path.join(root, sub))
import overlay_fixture as of
from button_glyphs import engine
from test_overlay_patch import Cutter
cfg = json.load(open(sys.argv[4]))
profile = of.make_profile(cfg["entries"], cfg["art"], vehicle=cfg["vehicle"])
eng = engine.Engine(home=cfg["home"], settings_dir=cfg["home"] + "/settings", runtime_dir=cfg["home"] + "/runtime",
                    glyph_dir=cfg["glyphs"], profiles={"204340": profile}, proc_root=cfg["home"] + "/proc")
with Cutter([cfg["install"], cfg["home"] + "/runtime/backups"], k=k, hard=True):
    eng.set_style("204340", mode)
"""


class HardKillTest(OverlayCase):
    """A power cut does not run `finally` blocks: the child process dies with os._exit at write event k."""

    def run_child(self, mode, k):
        import json
        import subprocess
        cfgfile = os.path.join(self.home, "child.json")
        with open(cfgfile, "w") as f:
            json.dump({"entries": self.entries, "art": self.art, "vehicle": self.vehicle, "home": self.home,
                       "install": self.install, "glyphs": GLYPH_DIR}, f)
        return subprocess.run([sys.executable, "-c", CHILD, ROOT, mode, str(k), cfgfile],
                              capture_output=True, timeout=120).returncode

    def count(self, mode):
        with Cutter([self.install, self.eng.backup_root]) as inj:
            self.set(mode)
        return inj.count

    def test_hard_kill_at_every_event_of_apply_and_of_original(self):
        n_apply = self.count("ps")
        self.ok("original")
        for k in range(n_apply):
            self.clean_world()
            self.assertEqual(self.run_child("ps", k), 9, k)
            now = tree(self.install)
            for rel, v in self.pristine.items():
                self.assertEqual(now.get(rel), v, "game file damaged at k=%d" % k)
            self.assertTrue(self.eng.sync(APPID, reason="watch")["ok"], k)       # config says ps (saved before the sync)
            self.assertEqual(self.status()["state"], "ok", k)
            self.ok("original")
            self.assertEqual(tree(self.install), self.pristine, k)
        self.clean_world()
        self.ok("ps")
        n_orig = self.count("original")
        for k in range(n_orig):
            self.clean_world()
            self.ok("ps")
            self.assertEqual(self.run_child("original", k), 9, k)
            self.eng.sync_all("watch")
            self.assertEqual(tree(self.install), self.pristine, k)
            self.assertEqual(self.status()["state"], "ok", k)


class ToolsTest(OverlayCase):
    def test_clone_validate_runs_the_cycle_on_a_copy_and_checks_the_folder_listing(self):
        import clone_validate
        report = clone_validate.validate(APPID, self.install, profiles={APPID: self.profile}, glyph_dir=GLYPH_DIR,
                                         log=lambda *a: None)
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual(report["files"], ["All_PC_01.gro", "Movies_PC_01.gro", "Patch_02_100_800000.gro"])
        self.assertEqual(tree(self.install), self.pristine)

    def test_deck_probe_asks_only_for_the_games_own_archives_never_for_our_file(self):
        import deck_probe
        from button_glyphs.profiles import PROFILES
        req = deck_probe.build_request(PROFILES["41010"], "41010")
        names = [f["file"] for f in req["files"]]
        self.assertIn("Content/SeriousSamHD_TSE/All.gro", names)
        self.assertNotIn("Content/SeriousSamHD_TSE/zz_ButtonGlyphs.gro", names)
        self.assertTrue(all(f["sha256"] is None for f in req["files"]))              # presence only: archives are GBs
        self.assertEqual(req["folders"], ["Content/SeriousSamHD_TSE"])


class GameUpdateTest(OverlayCase):
    """A game update changes a pinned entry while our archive is on disk: ours (built from the OLD originals, loaded last)
    would keep overriding the NEW ones, so a proven-ours file is removed by the same path as Original, and said."""

    def update_game(self):
        of.build_game(self.install, variant=1)

    def game_only(self):
        return {k: v for k, v in tree(self.install).items() if k not in self.ours()}

    def test_the_user_choice_after_an_update_removes_our_file_and_says_so(self):
        self.ok("ps")
        self.update_game()
        res = self.set("ps")
        self.assertFalse(res["ok"])
        self.assertIn("removed", res["errors"][0])
        self.assertIn("not the supported build", res["errors"][0])
        self.assertEqual(self.ours(), {})                        # our file is gone: the new originals show
        self.assertEqual(self.eng.load_config()["games"][APPID]["last_event"], "Overlay removed: the game files changed")
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertIn("removed", st["message"])

    def test_the_watcher_does_the_same_and_then_goes_quiet(self):
        self.ok("ps")
        self.update_game()
        self.eng.sync_all("watch")
        self.assertEqual(self.ours(), {})
        before = tree(self.install)
        with Cutter([self.install, self.eng.backup_root]) as inj:
            self.eng.sync_all("watch")                          # the next ticks are cheap and write nothing
        self.assertEqual(inj.count, 0)
        self.assertEqual(tree(self.install), before)
        self.assertEqual(self.status()["state"], "error")

    def test_the_game_files_are_never_written_by_the_removal(self):
        self.ok("ps")
        self.update_game()
        before = {k: v for k, v in tree(self.install).items() if k != OUR}
        self.eng.sync_all("watch")
        self.assertEqual(tree(self.install), before)

    def test_a_stale_file_of_ours_is_removed_too(self):
        self.ok("ps")
        of.make_art(self.art, shift=1)
        eng = self.new_engine(of.make_profile(self.entries, self.art, revision=2))
        self.update_game()
        eng.sync_all("watch")
        self.assertEqual(self.ours(), {})

    def test_a_file_we_cannot_prove_ours_is_left_alone(self):
        self.update_game()
        self.put(OUR, b"somebody else's archive")
        self.choose("ps")
        self.eng.sync_all("watch")
        self.assertFalse(self.set("ps")["ok"])
        self.assertEqual(rd(self.our_path()), b"somebody else's archive")

    def test_a_plain_read_failure_is_not_an_update_and_removes_nothing(self):
        self.ok("ps")
        with open(os.path.join(self.install, "Movies_PC_01.gro"), "wb") as f:
            f.write(b"PK\x03\x04 truncated")
        overlay_patch.clear_caches()
        res = self.set("ps")
        self.assertFalse(res["ok"])
        self.assertEqual(list(self.ours()), [OUR])

    def test_after_a_supported_build_is_back_the_watcher_applies_again(self):
        self.ok("ps")
        self.update_game()
        self.eng.sync_all("watch")
        of.build_game(self.install, variant=0)                   # Steam verify brings the pinned build back
        self.eng.sync_all("watch")
        self.assertEqual(list(self.ours()), [OUR])
        self.assertEqual(self.status()["state"], "ok")

    def test_original_chosen_after_an_update_removes_ours_without_reading_the_game(self):
        self.ok("ps")
        self.update_game()
        self.ok("original")
        self.assertEqual(self.ours(), {})

    def test_cut_at_every_event_of_the_removal_then_the_next_tick_finishes(self):
        self.ok("ps")
        self.update_game()
        with Cutter([self.install, self.eng.backup_root]) as inj:
            self.eng.sync_all("watch")
        n = inj.count
        self.assertGreaterEqual(n, 1)
        for k in range(n):
            self.clean_world()
            self.ok("ps")
            self.update_game()
            try:
                with Cutter([self.install, self.eng.backup_root], k=k):
                    self.eng.sync_all("watch")
            except Crash:
                pass
            self.assertEqual(self.game_only(), {kk: v for kk, v in tree(self.install).items() if kk != OUR}, k)
            self.eng.sync_all("watch")
            self.assertEqual(self.ours(), {}, k)


class GameUpdateLooseTest(OverlayCase):
    vehicle = "loose"

    def update_game(self):
        of.build_game(self.install, variant=1)

    def test_the_user_choice_after_an_update_removes_the_loose_files_and_says_so(self):
        self.ok("ps")
        self.update_game()
        res = self.set("ps")
        self.assertFalse(res["ok"])
        self.assertIn("removed", res["errors"][0])
        self.assertEqual(self.ours(), {})

    def test_the_loose_files_and_the_folders_we_made_are_removed(self):
        self.ok("ps")
        self.update_game()
        self.eng.sync_all("watch")
        self.assertEqual(self.ours(), {})                        # files and folders (tree lists both)

    def test_cut_at_every_event_of_the_removal_then_the_next_tick_finishes(self):
        self.ok("ps")
        self.update_game()
        with Cutter([self.install, self.eng.backup_root]) as inj:
            self.eng.sync_all("watch")
        for k in range(inj.count):
            self.clean_world()
            self.ok("ps")
            self.update_game()
            try:
                with Cutter([self.install, self.eng.backup_root], k=k):
                    self.eng.sync_all("watch")
            except Crash:
                pass
            self.eng.sync_all("watch")
            self.assertEqual(self.ours(), {}, k)


class BackoffTest(OverlayCase):
    """The watcher waits after ANY failed apply (a full disk, an OSError), checks the room from the pinned sizes BEFORE
    building, and an explicit user choice retries at once."""

    def spy_builds(self):
        calls = []
        real = overlay_patch.build_plan

        def spy(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        return calls, mock.patch.object(overlay_patch, "build_plan", spy)

    def test_a_full_disk_costs_no_build_and_the_second_tick_does_nothing(self):
        self.choose("ps")
        calls, patch = self.spy_builds()
        with patch, mock.patch("shutil.disk_usage", return_value=mock.Mock(free=1000)):
            self.assertFalse(self.eng.sync(APPID, reason="watch")["ok"])
            self.assertEqual(len(calls), 0)                     # the room check came BEFORE the build
            self.assertIn("overlay_retry_after", self.eng.load_config()["games"][APPID])
            self.assertFalse(self.eng.sync(APPID, reason="watch")["ok"])
            self.assertEqual(len(calls), 0)
        self.assertEqual(self.ours(), {})
        self.ok("ps")                                           # the user frees space and chooses: retried at once
        self.assertEqual(self.status()["state"], "ok")
        self.assertNotIn("overlay_retry_after", self.eng.load_config()["games"][APPID])

    def test_an_oserror_while_writing_arms_the_wait_and_the_next_tick_does_not_rebuild(self):
        self.choose("ps")
        calls, patch = self.spy_builds()
        outputs = []
        real_outputs = OverlayGlyphProfile.outputs

        def counting(profile, style, originals):
            outputs.append(1)
            return real_outputs(profile, style, originals)

        def fail(path, data, **kw):
            raise OSError(28, "No space left on device")

        with patch, mock.patch.object(OverlayGlyphProfile, "outputs", counting), \
                mock.patch.object(overlay_patch.files_patch, "write_atomic", fail):
            self.assertFalse(self.eng.sync(APPID, reason="watch")["ok"])
            n_builds, n_outputs = len(calls), len(outputs)
            self.assertGreaterEqual(n_builds, 1)
            self.assertIn("overlay_retry_after", self.eng.load_config()["games"][APPID])
            for _ in range(3):
                self.assertFalse(self.eng.sync(APPID, reason="watch")["ok"])
            self.assertEqual((len(calls), len(outputs)), (n_builds, n_outputs))     # 3 more ticks: no more builds
            self.assertFalse(self.set("ps")["ok"])
            self.assertGreater(len(calls), n_builds)                               # an explicit choice retries at once
        self.ok("ps")
        self.assertEqual(self.status()["state"], "ok")

    def test_the_wait_does_not_block_the_watcher_forever(self):
        self.choose("ps")
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=1000)):
            self.eng.sync(APPID, reason="watch")
        cfg = self.eng.load_config()
        cfg["games"][APPID]["overlay_retry_after"] = 1            # long ago
        self.eng.save_config(cfg)
        self.assertTrue(self.eng.sync(APPID, reason="watch")["ok"])
        self.assertEqual(list(self.ours()), [OUR])

    def test_the_room_estimate_comes_from_the_pinned_sizes(self):
        need = overlay_patch.estimate_bytes(self.profile)
        self.assertEqual(need, sum(e["size"] for e in self.entries) + (1 << 20))
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=need + overlay_patch.MIN_FREE - 1)):
            with self.assertRaises(RuntimeError):
                overlay_patch.check_room(self.profile, self.install)
        with mock.patch("shutil.disk_usage", return_value=mock.Mock(free=need + overlay_patch.MIN_FREE)):
            overlay_patch.check_room(self.profile, self.install)


class SurvivingMutantsTest(OverlayCase):
    def test_a_file_of_our_size_and_time_but_other_bytes_is_not_ours(self):
        self.ok("ps")
        info = os.stat(self.our_path())
        data = bytearray(rd(self.our_path()))
        data[len(data) // 2] ^= 1
        wr(self.our_path(), bytes(data))
        os.utime(self.our_path(), ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(os.stat(self.our_path()).st_size, info.st_size)
        self.assertEqual(os.stat(self.our_path()).st_mtime_ns, info.st_mtime_ns)
        st = self.status()
        self.assertNotEqual(st["state"], "ok")
        self.assertEqual(st["code"], "foreign_file")
        before = tree(self.install)
        self.assertFalse(self.set("ps")["ok"])
        self.assertEqual(tree(self.install), before)
        self.ok("original")
        self.assertEqual(tree(self.install), before)             # Original leaves what is not ours

    def test_a_dangling_symlink_at_our_path_is_foreign_not_missing(self):
        os.symlink(os.path.join(self.home, "nowhere"), self.our_path())
        self.assertTrue(os.path.islink(self.our_path()) and not os.path.exists(self.our_path()))
        self.choose("ps")
        self.assertEqual(self.status()["code"], "foreign_file")
        self.assertFalse(self.set("ps")["ok"])
        self.ok("original")
        self.assertTrue(os.path.islink(self.our_path()))
        self.assertFalse(os.path.exists(os.path.join(self.home, "nowhere")))        # never written through

    def order_of_events(self, old_vehicle, new_vehicle):
        self.ok("ps")
        eng = self.new_engine(of.make_profile(self.entries, self.art, vehicle=new_vehicle))
        events = []
        real_write, real_remove = files_patch.write_atomic, os.remove
        new_files = [overlay_patch.abspath(self.install, r) for r in overlay_patch.active_rels(
            of.make_profile(self.entries, self.art, vehicle=new_vehicle))]

        def write(path, data, **kw):
            real_write(path, data, **kw)
            if path.startswith(self.install):
                events.append(("write", path))

        def remove(path, *a, **kw):
            if path.startswith(self.install) and os.path.lexists(path) and not path.endswith(".tmp"):
                events.append(("remove", path, all(os.path.exists(p) for p in new_files)))
            return real_remove(path, *a, **kw)

        with mock.patch.object(overlay_patch.files_patch, "write_atomic", write), mock.patch.object(os, "remove", remove):
            self.assertTrue(eng.set_style(APPID, "ps")["ok"])
        return events

    def test_the_vehicle_switch_removes_the_old_artefact_last_gro_to_loose(self):
        events = self.order_of_events("gro", "loose")
        kinds = [e[0] for e in events]
        self.assertIn("remove", kinds)
        self.assertEqual(kinds, sorted(kinds, key=lambda k: k == "remove"))          # every write before the first remove
        self.assertTrue(all(e[2] for e in events if e[0] == "remove"))              # the new files existed at that moment

    def test_the_vehicle_switch_removes_the_old_artefact_last_loose_to_gro(self):
        self.profile = of.make_profile(self.entries, self.art, vehicle="loose")
        self.eng = self.new_engine()
        events = self.order_of_events("loose", "gro")
        kinds = [e[0] for e in events]
        self.assertEqual(kinds.count("write"), 1)
        self.assertEqual(kinds, sorted(kinds, key=lambda k: k == "remove"))
        self.assertTrue(all(e[2] for e in events if e[0] == "remove"))


class OwnershipTest(OverlayCase):
    """Decky runs as root: what we create in the game folder gets the game folder's owner and a neighbour's mode, set on
    the TEMP file before the rename (no crash window leaves a root-owned final file)."""

    def spy(self):
        events = []
        real_replace = os.replace

        def chown(path, uid, gid):
            events.append(("chown", path, uid, gid))

        def replace(src, dst, *a, **kw):
            events.append(("replace", src, dst))
            return real_replace(src, dst, *a, **kw)

        return events, [mock.patch.object(os, "geteuid", lambda: 0), mock.patch.object(os, "chown", chown),
                        mock.patch.object(os, "replace", replace)]

    def run_spied(self, fn):
        events, patches = self.spy()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        fn()
        for p in patches:
            p.stop()
            self.cleanups_done = True
        return events

    def owner(self):
        info = os.stat(self.install)
        return info.st_uid, info.st_gid

    def test_the_temp_file_is_chowned_before_it_is_renamed_and_gets_the_neighbours_mode(self):
        os.chmod(os.path.join(self.install, "All_PC_01.gro"), 0o640)
        events = self.run_spied(lambda: self.ok("ps"))
        ours = [i for i, e in enumerate(events) if e[0] == "replace" and e[2] == self.our_path()]
        self.assertEqual(len(ours), 1)
        tmp = events[ours[0]][1]
        chowns = [i for i, e in enumerate(events) if e[0] == "chown" and e[1] == tmp]
        self.assertEqual(len(chowns), 1)
        self.assertLess(chowns[0], ours[0])                                   # BEFORE the rename
        self.assertEqual(events[chowns[0]][2:], self.owner())                  # the game folder's owner
        self.assertEqual(stat_mode(self.our_path()), 0o640)                    # copied from All_PC_01.gro (read bits only)

    def test_not_root_nothing_is_chowned(self):
        events = []
        with mock.patch.object(os, "chown", lambda *a: events.append(a)):
            self.ok("ps")
        self.assertEqual(events, [])

    def test_the_ledger_and_records_in_the_plugins_folder_are_not_chowned(self):
        events = self.run_spied(lambda: self.ok("ps"))
        for e in events:
            if e[0] == "chown":
                self.assertFalse(e[1].startswith(self.eng.backup_root), e)

    def test_every_replace_into_the_game_folder_was_prepared_first_at_every_cut(self):
        with Cutter([self.install, self.eng.backup_root]) as inj:
            self.ok("ps")
        for k in range(inj.count):
            self.clean_world()
            events, patches = self.spy()
            for p in patches:
                p.start()
            try:
                try:
                    with Cutter([self.install, self.eng.backup_root], k=k):
                        self.set("ps")
                except Crash:
                    pass
            finally:
                for p in patches:
                    p.stop()
            chowned = {e[1] for e in events if e[0] == "chown"}
            for e in events:
                if e[0] == "replace" and e[2].startswith(self.install):
                    self.assertIn(e[1], chowned, "a file reached its final name unprepared (k=%d)" % k)

    def test_loose_folders_and_files_all_get_the_owner(self):
        self.profile = of.make_profile(self.entries, self.art, vehicle="loose")
        self.eng = self.new_engine()
        events = self.run_spied(lambda: self.ok("ps"))
        chowned = {e[1] for e in events if e[0] == "chown"}
        made = [os.path.join(self.install, "Content"), os.path.join(self.install, "Content", "SeriousSam2"),
                os.path.join(self.install, "Content", "SeriousSam2", "Textures"),
                os.path.join(self.install, "Content", "SeriousSam2", "Textures", "Menu")]
        self.assertTrue(set(made) <= chowned or all(os.path.isdir(m) for m in made))
        for d in made[2:]:                                                    # the ones that did not exist before
            self.assertIn(d, chowned)
            self.assertEqual(stat_mode(d), 0o755)
        for e in self.entries:
            final = os.path.join(self.install, *e["path"].split("/"))
            replaces = [x for x in events if x[0] == "replace" and x[2] == final]
            self.assertEqual(len(replaces), 1)
            self.assertIn(replaces[0][1], chowned)
        for e in events:
            if e[0] == "chown":
                self.assertEqual(e[2:], self.owner())


def stat_mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class ProfileTableTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        of.make_art(os.path.join(self.d, "art"))
        self.entries = [{"key": k, "path": of.PREFIX + k + ".tex", "size": 100, "sha256": "0" * 64, "gro": "x.gro",
                         "width": 16, "height": 16} for k, _, _ in of.KEYS]

    def make(self, **kw):
        entries = kw.pop("entries", self.entries)
        return of.make_profile(entries, os.path.join(self.d, "art"), **kw)

    def test_table_mistakes_fail_at_construction(self):
        with self.assertRaises(ValueError):
            self.make(entries=self.entries + [dict(self.entries[0])])
        with self.assertRaises(ValueError):
            self.make(entries=[dict(self.entries[0], path="../x.tex")] + self.entries[1:])
        with self.assertRaises(ValueError):
            self.make(entries=[dict(self.entries[0], sha256="12")] + self.entries[1:])
        with self.assertRaises(ValueError):
            self.make(vehicle="symlink")
        with self.assertRaises(ValueError):
            self.make(archive="sub/zz.gro")
        with self.assertRaises(ValueError):
            self.make(archive="zz.zip")
        with self.assertRaises(ValueError):
            self.make(entries=[])

    def test_the_flags_the_engine_dispatches_on(self):
        p = self.make()
        self.assertTrue(p.overlay_level)
        self.assertFalse(getattr(p, "files_level", False) or getattr(p, "region_level", False))
        self.assertEqual(p.file, "zz_ButtonGlyphs.gro")

    def test_unknown_style_is_refused(self):
        p = self.make()
        with self.assertRaises(ValueError):
            p.outputs("switch", {})


if __name__ == "__main__":
    unittest.main()
