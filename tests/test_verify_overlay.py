"""tools/verify_profile.py on the add-file overlay (OverlayKind): every probe green on correct profiles, and red when a
safety property is removed (mutation tests). Synthetic Croteam-style games only; the heavy runs on real copies are CLI runs.
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("py_modules", "tests", "tools"):
    sys.path.insert(0, os.path.join(ROOT, sub))

import verify_fixtures as vf  # noqa: E402
import verify_profile as vp  # noqa: E402
import verify_worlds as vw  # noqa: E402
from button_glyphs import files_patch, overlay_patch  # noqa: E402

QUIET = lambda *a, **k: None  # noqa: E731
ALL = [p for p in vp.PROBES if p != "catalog"]


class Fixture(unittest.TestCase):
    appid = vf.OVERLAY_APPID
    factory = "verify_fixtures:overlay_profiles"
    vehicle = "gro"

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp()
        cls.src = cls.make_src()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    @classmethod
    def make_src(cls):
        return vf.overlay_fixture(cls.base)

    @property
    def arg(self):
        return "%s|%s" % (self.base, self.vehicle)

    def profiles(self):
        return vf.overlay_profiles(self.arg)

    def run_probes(self, only, **kw):
        args = vp.make_args(quick=True, only=only, profiles=self.factory, profiles_arg=self.arg, **kw)
        return vp.run(self.appid, self.src, self.profiles(), args, log=QUIET)

    def statuses(self, report):
        return {p["name"]: p["status"] for p in report["probes"]}

    def probe(self, report, name):
        return next(p for p in report["probes"] if p["name"] == name)


class GreenRunTest(Fixture):
    def test_every_probe_passes_on_a_correct_overlay_profile(self):
        rep = self.run_probes(ALL)
        bad = [(p["name"], p["details"]) for p in rep["probes"] if p["status"] == "FAIL"]
        self.assertEqual(bad, [])
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["kind"], "overlay")
        st = self.statuses(rep)
        for name in ("pinned", "cycle", "revision-upgrade", "running", "steam-verify", "new-build", "unknown-build",
                     "assets", "crash", "symlinks", "ledger", "timings"):
            self.assertEqual(st[name], "PASS", (name, self.probe(rep, name)["details"]))
        self.assertEqual(st["visual"], "SKIP")
        self.assertEqual(self.probe(rep, "cycle")["number"].split(",")[1].strip(), "1 added file(s) changed")

    def test_the_cycle_counts_the_start_files_that_stay_identical_exactly(self):
        rep = self.run_probes(["cycle"])
        n = self.probe(rep, "cycle")["number"]
        ident = int(n.rsplit(",", 1)[1].split()[0])
        # the world holds the cloned archives only; our added file is NOT one of the start's files
        self.assertEqual(ident, len([f for f in os.listdir(self.src) if f.endswith(".gro")]))

    def test_the_visual_probe_makes_sheets_when_asked(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("Pillow is not installed")
        sheet = os.path.join(self.base, "sheets")
        rep = self.run_probes(["visual"], sheet=sheet)
        self.assertEqual(self.statuses(rep)["visual"], "PASS", self.probe(rep, "visual")["details"])
        self.assertTrue(os.listdir(sheet))

    def test_the_timings_probe_reports_the_status_and_idle_tick_costs(self):
        rep = self.run_probes(["timings"])
        data = self.probe(rep, "timings")["data"]["median_ms"]
        self.assertIn("status", data)
        self.assertIn("idle tick", data)

    def test_a_missing_archive_folder_is_a_plain_error(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        rep = vp.run(self.appid, empty, self.profiles(), vp.make_args(quick=True, only=["pinned"]), log=QUIET)
        self.assertIn("no .gro archive", rep["error"])

    def test_a_copy_that_differs_from_the_pinned_table_fails_pinned_and_skips_the_rest(self):
        src = os.path.join(self.base, "other-build")
        shutil.copytree(self.src, src)
        import overlay_fixture as of
        of.build_game(src, variant=1)
        args = vp.make_args(quick=True, only=["pinned", "cycle"], profiles=self.factory, profiles_arg=self.arg)
        rep = vp.run(self.appid, src, self.profiles(), args, log=QUIET)
        st = self.statuses(rep)
        self.assertEqual((st["pinned"], st["cycle"]), ("FAIL", "SKIP"))


class LooseVehicleRunTest(Fixture):
    vehicle = "loose"

    def test_the_loose_vehicle_passes_the_probes_that_see_folders_and_files(self):
        rep = self.run_probes(["pinned", "cycle", "steam-verify", "unknown-build", "symlinks", "ledger", "crash"])
        bad = [(p["name"], p["details"]) for p in rep["probes"] if p["status"] == "FAIL"]
        self.assertEqual(bad, [])
        # three files plus the folders the loose vehicle creates are the units the cycle watches
        self.assertIn("added file(s) changed", self.probe(rep, "cycle")["number"])


class HdRunTest(Fixture):
    appid = vf.HD_APPID
    factory = "verify_fixtures:hd_profiles"

    @classmethod
    def make_src(cls):
        return vf.hd_fixture(cls.base)

    def profiles(self):
        return vf.hd_profiles(self.arg)

    def test_the_folder_may_be_the_mount_folder_itself_and_the_probes_pass(self):
        rep = self.run_probes(["pinned", "cycle", "steam-verify", "unknown-build", "assets", "symlinks", "ledger"])
        bad = [(p["name"], p["details"]) for p in rep["probes"] if p["status"] == "FAIL"]
        self.assertEqual(bad, [])
        self.assertIn("folder taken as the profile's Content/SeriousSamHD_X folder", self.probe(rep, "pinned")["details"][0])


class MutationTest(Fixture):
    """A probe that cannot fail is worthless: remove one safety property and the probe must report FAIL."""

    def fails(self, only):
        rep = self.run_probes(only)
        return {p["name"]: p["status"] for p in rep["probes"]}

    def test_an_overlay_that_also_writes_a_game_file_fails_cycle(self):
        real = overlay_patch.apply

        def sneaky(profile, install, backup_dir, plan_, style):
            real(profile, install, backup_dir, plan_, style)
            with open(os.path.join(install, "Sam2.exe"), "ab") as f:      # a write into a game file
                f.write(b"x")

        with mock.patch.object(overlay_patch, "apply", sneaky):
            self.assertEqual(self.fails(["cycle"])["cycle"], "FAIL")

    def test_an_apply_that_never_records_ownership_fails_cycle(self):
        with mock.patch.object(files_patch, "record_renders", lambda *a, **k: None):
            self.assertEqual(self.fails(["cycle"])["cycle"], "FAIL")

    def test_a_non_atomic_writer_fails_crash(self):
        def torn(path, data):
            with open(path, "wb") as f:
                f.write(data)

        with mock.patch.object(files_patch, "write_atomic", torn):
            self.assertEqual(self.fails(["crash"])["crash"], "FAIL")

    def test_an_engine_that_replaces_a_foreign_file_fails_the_foreign_probe(self):
        real = overlay_patch._classify

        def everything_is_ours(path, known, builds, fps, wants=None):
            state, style, digest = real(path, known, builds, fps, wants)
            return (overlay_patch.STALE, None, digest) if state == overlay_patch.UNKNOWN else (state, style, digest)

        with mock.patch.object(overlay_patch, "_classify", everything_is_ours):
            self.assertEqual(self.fails(["symlinks"])["symlinks"], "FAIL")

    def test_a_guard_that_sees_nothing_fails_running(self):
        from button_glyphs import steam
        with mock.patch.object(steam, "is_running", lambda *a, **k: False):
            self.assertEqual(self.fails(["running"])["running"], "FAIL")

    def test_a_watcher_that_never_finishes_original_fails_crash(self):
        from button_glyphs import engine
        with mock.patch.object(engine.Engine, "_overlay_restore_pending", lambda self, appid: False):
            self.assertEqual(self.fails(["crash"])["crash"], "FAIL")

    def test_a_builder_that_swallows_a_damaged_picture_fails_assets(self):
        from button_glyphs.profiles.overlay_glyphs import OverlayGlyphProfile
        real = OverlayGlyphProfile.outputs

        def lenient(self, style, originals):
            try:
                return real(self, style, originals)
            except Exception:
                return dict(originals)                         # draws nothing but "succeeds"

        with mock.patch.object(OverlayGlyphProfile, "preflight", lambda self: None), \
                mock.patch.object(OverlayGlyphProfile, "outputs", lenient):
            self.assertEqual(self.fails(["assets"])["assets"], "FAIL")


class BuildingBlocksTest(unittest.TestCase):
    def test_the_backup_bookkeeping_exclusion_is_the_overlays_alone(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for name in ("renders.log", "builds.log", "dirs.log", "x.orig"):
            with open(os.path.join(d, name), "wb") as f:
                f.write(b"x")
        from pathlib import Path

        class W:
            backup_root = Path(d)

            def backups_exist(self):
                return True

        for kind, expect in (("files", {"builds.log", "dirs.log", "x.orig"}), ("overlay", {"x.orig"})):
            w = W()
            w.kind = type("K", (), {"name": kind})()
            self.assertEqual(set(vp.backups_of(w)), expect, kind)

    def test_deck_probe_asks_for_the_overlays_own_size_not_the_archives(self):
        import deck_probe
        from button_glyphs.profiles import PROFILES
        for appid in ("204340", "41000", "41010"):
            req = deck_probe.build_request(PROFILES[appid], appid)
            self.assertEqual(req["apply_bytes"], overlay_patch.estimate_bytes(PROFILES[appid]))
            self.assertLess(req["apply_bytes"], 20 << 20)
            self.assertGreater(sum(f["size"] for f in req["files"]), 100 << 20)        # the archives are far bigger

    def test_crash_injector_counts_deletions_but_not_the_removal_of_nothing(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "f")
        with open(path, "wb") as f:
            f.write(b"x")
        os.mkdir(os.path.join(d, "dir"))
        with vw.CrashInjector([d]) as inj:
            for p in (os.path.join(d, "none"),):
                try:
                    os.remove(p)
                except OSError:
                    pass
            os.remove(path)
            os.rmdir(os.path.join(d, "dir"))
        self.assertEqual(inj.count, 2)
        with open(path, "wb") as f:
            f.write(b"x")
        with self.assertRaises(vw.Crash):
            with vw.CrashInjector([d], k=0):
                os.remove(path)
        self.assertTrue(os.path.exists(path))                       # the cut came BEFORE the deletion

    def test_kind_name_knows_the_overlay(self):
        from button_glyphs.profiles import PROFILES
        for appid in ("204340", "41000", "41010"):
            self.assertEqual(vw.kind_name(PROFILES[appid]), "overlay")

    def test_the_overlay_tree_lists_folders_and_hashes_big_archives_once(self):
        import overlay_fixture as of
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        of.build_game(os.path.join(d, "src"))
        profile = vf.overlay_profiles("%s|gro" % d)[vf.OVERLAY_APPID]
        kind = vw.OverlayKind(profile, vp.make_args())
        kind.resolve(os.path.join(d, "src"))

        class W:
            install = type(os.path)(os.path.join(d, "src")) if False else __import__("pathlib").Path(d, "src")
            start = {}

        w = W()
        t1 = kind.tree(w)
        self.assertEqual(t1["Content"], "DIR")
        calls = []
        real = vw.sha_file
        with mock.patch.object(vw, "sha_file", lambda p, *a: calls.append(p) or real(p, *a)):
            kind.tree(w)
        self.assertTrue(all(os.path.getsize(p) <= (1 << 20) for p in calls), calls)   # only small files re-read


if __name__ == "__main__":
    unittest.main()
