"""tools/verify_profile.py on synthetic games of every strategy kind, and proof that each probe can go red.

Fixtures are tiny (tests/verify_fixtures.py); no game data. The heavy runs on real files are CLI runs, not tests.
A probe that cannot fail is worthless, so every safety probe has a mutation test here: the safety property is
removed from the engine (monkeypatch) and the probe must report FAIL.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import verify_fixtures as vf  # noqa: E402
import verify_profile as vp  # noqa: E402
import verify_worlds as vw  # noqa: E402
from button_glyphs import engine, files_patch, region_patch, steam  # noqa: E402

QUIET = lambda *a, **k: None  # noqa: E731
ALL = [p for p in vp.PROBES if p != "catalog"]       # the catalog probe runs the (slow) real build --check: mocked below


class Fixture(unittest.TestCase):
    """A synthetic game of one kind plus a way to run probes on it (quick mode, no console output)."""
    kind = None

    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp()
        cls.build()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    @classmethod
    def build(cls):
        raise NotImplementedError

    def run_probes(self, only, **kw):
        args = vp.make_args(quick=True, only=only, profiles=self.factory, profiles_arg=self.arg, **kw)
        return vp.run(self.appid, self.src, self.profiles(), args, log=QUIET)

    def profiles(self):
        raise NotImplementedError

    def statuses(self, report):
        return {p["name"]: p["status"] for p in report["probes"]}

    def probe(self, report, name):
        return next(p for p in report["probes"] if p["name"] == name)


class FilesFixture(Fixture):
    appid = vf.FILES_APPID
    factory = "verify_fixtures:files_profiles"

    @classmethod
    def build(cls):
        cls.src, cls.arg = vf.files_fixture(cls.base)

    def profiles(self):
        return vf.files_profiles(self.arg)


class RegionsFixture(Fixture):
    appid = vf.REGIONS_APPID
    factory = "verify_fixtures:regions_profiles"
    arg = None

    @classmethod
    def build(cls):
        cls.src = vf.regions_fixture(cls.base)

    def profiles(self):
        return vf.regions_profiles()


# ------------------------------------------------------------------------------------ a green run of every kind


class FilesKindTest(FilesFixture):
    def test_every_probe_passes_on_a_correct_profile(self):
        rep = self.run_probes(ALL)
        bad = [(p["name"], p["details"]) for p in rep["probes"] if p["status"] == "FAIL"]
        self.assertEqual(bad, [])
        self.assertTrue(rep["ok"])
        st = self.statuses(rep)
        for name in ("pinned", "cycle", "revision-upgrade", "running", "steam-verify", "new-build", "unknown-build",
                     "assets", "crash", "symlinks", "ledger", "timings"):
            self.assertEqual(st[name], "PASS", name)
        self.assertEqual(st["visual"], "SKIP")                     # no --sheet: said, never silently passed
        self.assertIn("--sheet", self.probe(rep, "visual")["details"][0])
        self.assertEqual(self.probe(rep, "cycle")["number"].split(",")[1].strip(), "6 file(s) changed")

    def test_the_folder_may_be_the_profiles_own_folder(self):
        """Songs for a Hero is copied as its Content folder, not as an install root."""
        content = os.path.join(self.src, "Content")
        rep = vp.run(self.appid, content, self.profiles(), vp.make_args(quick=True, only=["pinned", "cycle"]), log=QUIET)
        self.assertTrue(rep["ok"], rep)
        self.assertIn("folder taken as the profile's Content/ folder", self.probe(rep, "pinned")["details"][0])

    def test_neighbours_are_copied_and_must_stay_identical(self):
        rep = self.run_probes(["cycle"])
        self.assertIn("3 identical", self.probe(rep, "cycle")["number"])        # the 3 untouched textures

    def test_crash_probe_survives_tiny_torn_writes_when_the_sector_is_tiny(self):
        """With a 16-byte 'sector' even a 2 KB file write can be torn: the temp+rename engine must still recover."""
        with mock.patch.object(vw, "SECTOR", 16):
            rep = self.run_probes(["crash"])
        self.assertEqual(self.statuses(rep)["crash"], "PASS", rep["probes"][0]["details"])


class RegionsKindTest(RegionsFixture):
    def test_every_probe_passes_on_a_correct_profile(self):
        rep = self.run_probes(ALL)
        self.assertEqual([p["name"] for p in rep["probes"] if p["status"] == "FAIL"], [])
        st = self.statuses(rep)
        for name in ("pinned", "cycle", "revision-upgrade", "running", "steam-verify", "new-build", "unknown-build",
                     "assets", "crash", "timings"):
            self.assertEqual(st[name], "PASS", name)
        self.assertEqual(st["symlinks"], "SKIP")
        self.assertEqual(st["ledger"], "SKIP")
        self.assertIn("loose-file", self.probe(rep, "symlinks")["details"][0])

    def test_the_stand_in_archive_has_holes_sentinels_and_the_real_size(self):
        args = vp.make_args(archive_size=10 << 30)
        profile = self.profiles()[self.appid]
        kind = vw.RegionsKind(profile, args)
        kind.resolve(self.src)
        self.assertEqual(kind.total, 10 << 30)
        spans = [(r["offset"], r["offset"] + r["size"]) for r in profile.regions]
        for off, data in kind.sentinels:
            self.assertEqual(len(data), vw.SENTINEL)
            self.assertTrue(all(off + len(data) <= b or off >= e for b, e in spans), "a sentinel overlaps a region")
        for off in kind.holes:
            self.assertTrue(all(off + vw.SENTINEL <= b or off >= e for b, e in spans))
        # deterministic: a second resolve gives the same sentinels
        again = vw.RegionsKind(profile, args)
        again.resolve(self.src)
        self.assertEqual(kind.sentinels, again.sentinels)

    def test_legacy_names_and_missing_regions(self):
        legacy = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, legacy, True)
        for i, r in enumerate(self.profiles()[self.appid].regions):
            shutil.copyfile(os.path.join(self.src, r["key"].replace("/", "_") + ".bin"), os.path.join(legacy, "r%d.bin" % i))
        rep = vp.run(self.appid, legacy, self.profiles(), vp.make_args(quick=True, only=["pinned"]), log=QUIET)
        self.assertTrue(rep["ok"])
        self.assertIn("r<i>.bin", rep["probes"][0]["details"][0])
        os.remove(os.path.join(legacy, "r1.bin"))
        rep = vp.run(self.appid, legacy, self.profiles(), vp.make_args(only=["pinned"]), log=QUIET)
        self.assertIn("no region file", rep["error"])
        self.assertFalse(rep["ok"])


class OtherKindsTest(unittest.TestCase):
    """entries / il / config: the subset of probes that applies, each green on a correct profile."""

    def run_kind(self, appid, src, profiles, factory, only=None):
        args = vp.make_args(quick=True, only=only or ALL, profiles=factory)
        return vp.run(appid, src, profiles, args, log=QUIET)

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, True)

    def test_entries(self):
        rep = self.run_kind(vf.ENTRIES_APPID, vf.entries_fixture(self.base), vf.entries_profiles(),
                            "verify_fixtures:entries_profiles")
        st = {p["name"]: p for p in rep["probes"]}
        self.assertEqual([n for n, p in st.items() if p["status"] == "FAIL"], [], [p["details"] for p in st.values()])
        for name in ("pinned", "cycle", "revision-upgrade", "running", "steam-verify", "new-build", "unknown-build", "crash"):
            self.assertEqual(st[name]["status"], "PASS", name)
        self.assertEqual(st["assets"]["status"], "SKIP")
        self.assertIn("no bundled art", st["assets"]["details"][0])

    def test_entries_crash_probe_in_full_mode(self):
        """Every sampled power cut (exception and hard os._exit) of apply and Original recovers, not only the quick ones."""
        args = vp.make_args(quick=False, only=["crash"], profiles="verify_fixtures:entries_profiles")
        rep = vp.run(vf.ENTRIES_APPID, vf.entries_fixture(self.base), vf.entries_profiles(), args, log=QUIET)
        st = {p["name"]: p for p in rep["probes"]}
        self.assertEqual(st["crash"]["status"], "PASS", st["crash"]["details"])

    def test_a_watcher_that_does_not_resume_a_cut_original_makes_crash_fail(self):
        for appid, factory, fixture, profiles in (
                (vf.ENTRIES_APPID, "verify_fixtures:entries_profiles", vf.entries_fixture, vf.entries_profiles),
                (vf.IL_APPID, None, vf.il_fixture, vf.il_profiles)):
            with mock.patch.object(engine.Engine, "_marker_restore_pending", return_value=False):
                rep = self.run_kind(appid, fixture(tempfile.mkdtemp(dir=self.base)), profiles(), factory, only=["crash"])
            crash = next(p for p in rep["probes"] if p["name"] == "crash")
            self.assertEqual(crash["status"], "FAIL", appid)
            self.assertTrue(any("watcher did not resume a cut Original" in d for d in crash["details"]), appid)

    def test_il_patch(self):
        rep = self.run_kind(vf.IL_APPID, vf.il_fixture(self.base), vf.il_profiles(), None)
        st = {p["name"]: p for p in rep["probes"]}
        self.assertEqual([n for n, p in st.items() if p["status"] == "FAIL"], [], [p["details"] for p in st.values()])
        for name in ("pinned", "cycle", "revision-upgrade", "running", "steam-verify", "new-build", "unknown-build", "crash"):
            self.assertEqual(st[name]["status"], "PASS", name)
        # the engine backs the file up (in its own folder) before it refuses an unknown one: said, not hidden
        self.assertTrue(any("backs the file up" in d for d in st["unknown-build"]["details"]))

    def test_config_value(self):
        rep = self.run_kind(vf.CONFIG_APPID, vf.config_fixture(self.base), vf.config_profiles(),
                            "verify_fixtures:config_profiles")
        st = {p["name"]: p for p in rep["probes"]}
        self.assertEqual([n for n, p in st.items() if p["status"] == "FAIL"], [], [p["details"] for p in st.values()])
        for name in ("pinned", "cycle", "running", "new-build", "unknown-build", "crash"):
            self.assertEqual(st[name]["status"], "PASS", name)
        self.assertEqual(st["steam-verify"]["status"], "SKIP")
        self.assertEqual(st["revision-upgrade"]["status"], "SKIP")

    def test_config_game_whose_save_already_holds_the_xbox_value(self):
        """523 is the real default: Xbox then changes nothing, and the cycle must expect exactly that."""
        src = vf.config_fixture(self.base, value="523.000000")
        rep = self.run_kind(vf.CONFIG_APPID, src, vf.config_profiles(), "verify_fixtures:config_profiles",
                            only=["pinned", "cycle", "crash"])
        self.assertTrue(rep["ok"], [p["details"] for p in rep["probes"]])


# ------------------------------------------------------------------------------------ mutation: probes must go red


class MutationTest(FilesFixture):
    """Each test removes one safety property from the engine and expects the probe to say FAIL."""

    def fails(self, only, **kw):
        rep = self.run_probes(only, **kw)
        self.assertEqual(self.statuses(rep)[only[0]], "FAIL", self.probe(rep, only[0]))
        self.assertFalse(rep["ok"])
        return self.probe(rep, only[0])

    def test_a_non_atomic_writer_makes_crash_fail(self):
        def in_place(path, data):                 # the sloppy version: overwrite the file itself
            with open(path, "r+b" if os.path.exists(path) else "wb") as f:
                f.write(data)

        with mock.patch.object(vw, "SECTOR", 16), mock.patch.object(files_patch, "write_atomic", in_place):
            probe = self.fails(["crash"])
        self.assertTrue(any("neither of its two whole versions" in d or "not the expected" in d for d in probe["details"]))

    def test_skipping_the_pinned_check_makes_unknown_build_fail(self):
        with mock.patch.object(files_patch, "check", lambda rows, want: ""):
            probe = self.fails(["unknown-build"])
        self.assertTrue(probe["details"])

    def test_a_broken_undo_makes_cycle_fail(self):
        with mock.patch.object(files_patch, "revert", lambda *a, **k: None):
            probe = self.fails(["cycle"])
        self.assertTrue(any("tree is not the start" in d or "state" in d for d in probe["details"]))

    def test_a_guard_that_sees_nothing_makes_running_fail(self):
        with mock.patch.object(steam, "is_running", lambda app, cmdlines: False):
            self.fails(["running"])

    def test_a_missing_preflight_makes_assets_fail(self):
        profile = self.profiles()[self.appid]
        with mock.patch.object(type(profile), "preflight", lambda self_: None):
            self.fails(["assets"])

    def test_an_apply_that_ignores_a_restored_file_makes_steam_verify_fail(self):
        real = files_patch.plan

        def blind(profile, install, backup_dir):                  # classifies every file as already patched
            return [files_patch.Row(r.entry, r.path, files_patch.PATCHED, "ps") for r in real(profile, install, backup_dir)]

        with mock.patch.object(files_patch, "plan", blind):
            self.fails(["steam-verify"])

    def test_a_stale_ledger_trust_makes_the_ledger_probe_fail(self):
        """If 'same header, different pixels' were enough to call a file ours, the pixel-only update case would be rewritten."""
        real = files_patch.classify

        def trusting(profile, entry, path, backup_dir, known):
            state, style = real(profile, entry, path, backup_dir, known)
            return (files_patch.STALE, None) if state == files_patch.UNKNOWN else (state, style)

        with mock.patch.object(files_patch, "classify", trusting):
            self.fails(["ledger"])

    def test_following_links_makes_the_symlinks_probe_fail(self):
        real = files_patch.classify

        def follows(profile, entry, path, backup_dir, known):
            return real(profile, entry, os.path.realpath(path), backup_dir, known)

        with mock.patch.object(files_patch, "classify", follows):
            self.fails(["symlinks"])

    def test_an_engine_that_does_not_defer_for_a_downloading_update_makes_new_build_fail(self):
        with mock.patch.object(steam, "is_ready", lambda app: True):
            probe = self.fails(["new-build"])
        self.assertTrue(any("did not defer" in d for d in probe["details"]))

    def test_a_pinned_table_mismatch_fails_pinned_and_skips_the_rest(self):
        copy = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, copy, True)
        shutil.copytree(self.src, os.path.join(copy, "g"))
        victim = os.path.join(copy, "g", "Content", "Tooltip", "A.xnb")
        with open(victim, "r+b") as f:
            f.seek(100)
            f.write(b"\x01")
        rep = vp.run(self.appid, os.path.join(copy, "g"), self.profiles(),
                     vp.make_args(quick=True, only=["pinned", "cycle"], profiles=self.factory, profiles_arg=self.arg), log=QUIET)
        st = self.statuses(rep)
        self.assertEqual((st["pinned"], st["cycle"]), ("FAIL", "SKIP"))
        self.assertIn("SHA-256 differs", rep["probes"][0]["details"][0])


class RegionMutationTest(RegionsFixture):
    def fails(self, only):
        rep = self.run_probes(only)
        self.assertEqual(self.statuses(rep)[only[0]], "FAIL", self.probe(rep, only[0]))
        return self.probe(rep, only[0])

    def test_writing_without_the_w_marker_makes_crash_fail(self):
        real = region_patch._put

        def no_marker(f, region, new, marker_state_first):
            return real(f, region, new, None)             # body and header first, no recovery marker

        with mock.patch.object(region_patch, "_put", no_marker), mock.patch.object(vw, "SECTOR", 4096):
            probe = self.fails(["crash"])
        self.assertTrue(any("neither of its two whole versions" in d for d in probe["details"]))

    def test_a_watcher_that_does_not_resume_a_cut_original_makes_crash_fail(self):
        with mock.patch.object(engine.Engine, "_marker_restore_pending", return_value=False):
            probe = self.fails(["crash"])
        self.assertTrue(any("watcher did not resume a cut Original" in d for d in probe["details"]))

    def test_a_write_outside_the_regions_is_seen_by_the_sentinels(self):
        real = region_patch.apply

        def sloppy(profile, install, backup_dir, rows, style):
            real(profile, install, backup_dir, rows, style)
            with open(os.path.join(install, profile.file), "r+b") as f:
                f.seek(2)
                f.write(b"\xaa")                         # 2 bytes into the archive's first sentinel

        with mock.patch.object(region_patch, "apply", sloppy):
            probe = self.fails(["cycle"])
        self.assertTrue(any("sentinel" in d for d in probe["details"]))

    def test_skipping_the_region_check_makes_unknown_build_fail(self):
        with mock.patch.object(region_patch, "check", lambda *a, **k: []):
            self.fails(["unknown-build"])

    def test_a_revision_that_is_not_checked_makes_revision_upgrade_fail(self):
        real = region_patch.plan

        def ignore_revision(profile, install, style):
            rows = real(profile, install, style)
            return [(r, s, m, ok or (s == region_patch.PATCHED and style != "original")) for r, s, m, ok in rows]

        with mock.patch.object(region_patch, "plan", ignore_revision):
            self.fails(["revision-upgrade"])


# ------------------------------------------------------------------------------------ the building blocks


class BuildingBlocksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_crash_injector_counts_tears_and_scopes(self):
        inside, outside = os.path.join(self.tmp, "in"), os.path.join(self.tmp, "out")
        os.makedirs(inside)
        os.makedirs(outside)
        with vw.CrashInjector([inside]) as inj:
            with open(os.path.join(inside, "a"), "wb") as f:
                f.write(b"x" * 10)
                f.write(b"y" * 10)
            with open(os.path.join(outside, "b"), "wb") as f:
                f.write(b"z")
            os.replace(os.path.join(inside, "a"), os.path.join(inside, "c"))
            os.replace(os.path.join(outside, "b"), os.path.join(outside, "d"))
        self.assertEqual(inj.count, 3)                      # two writes + one replace inside; nothing outside
        with mock.patch.object(vw, "SECTOR", 8):
            with self.assertRaises(vw.Crash):
                with vw.CrashInjector([inside], k=1):
                    with open(os.path.join(inside, "t"), "wb") as f:
                        f.write(b"a" * 4)
                        f.write(b"b" * 20)                  # event 1: torn, 10 bytes reach the disk
        with open(os.path.join(inside, "t"), "rb") as f:
            self.assertEqual(f.read(), b"a" * 4 + b"b" * 10)
        with mock.patch.object(vw, "SECTOR", 4096):          # a small write dies BEFORE it, whole
            with self.assertRaises(vw.Crash):
                with vw.CrashInjector([inside], k=0):
                    with open(os.path.join(inside, "u"), "wb") as f:
                        f.write(b"q" * 20)
        self.assertEqual(os.path.getsize(os.path.join(inside, "u")), 0)

    def test_forbidden_writes_count_attempts_and_restore_afterwards(self):
        real = files_patch.write_atomic
        forbid = vw.forbid_writes([(files_patch, "write_atomic")])
        with forbid.stack:
            with self.assertRaises(vw.WriteAttempt):
                files_patch.write_atomic("/nowhere", b"")
        self.assertEqual(forbid.hits, 1)
        self.assertIs(files_patch.write_atomic, real)

    def test_trailer_hash_ignores_the_time_stamp_only(self):
        from button_glyphs.trailer import write_trailer
        a, b = os.path.join(self.tmp, "a"), os.path.join(self.tmp, "b")
        for path in (a, b):
            with open(path, "wb") as f:
                f.write(b"data" * 100)
        write_trailer(a, {"style": "ps", "applied": 1, "buildid": "1"})
        write_trailer(b, {"style": "ps", "applied": 2, "buildid": "2"})
        self.assertEqual(vw.trailer_hash(a), vw.trailer_hash(b))
        c = os.path.join(self.tmp, "c")
        with open(c, "wb") as f:
            f.write(b"data" * 100)
        write_trailer(c, {"style": "xbox", "applied": 1, "buildid": "1"})
        self.assertNotEqual(vw.trailer_hash(a), vw.trailer_hash(c))

    def test_tree_of_names_links_and_never_reads_a_huge_sparse_file(self):
        os.makedirs(os.path.join(self.tmp, "d"))
        with open(os.path.join(self.tmp, "d", "f"), "wb") as f:
            f.write(b"1")
        os.symlink(os.path.join(self.tmp, "d", "f"), os.path.join(self.tmp, "l"))
        with open(os.path.join(self.tmp, "big"), "wb") as f:
            f.truncate(3 << 30)
        tree = vw.tree_of(self.tmp)
        self.assertEqual(set(tree), {"d/f", "l", "big"})
        self.assertTrue(tree["l"].startswith("LINK->"))
        self.assertEqual(tree["big"], "SPARSE:%d" % (3 << 30))

    def test_cached_profile_renders_once_and_delegates_the_rest(self):
        profile = vf.regions_profiles()[vf.REGIONS_APPID]
        cached = vw.CachedProfile(profile)
        region = profile.regions[0]
        data = vf.trg.texture(7)
        a = cached.render(data, region, "ps")
        b = cached.render(data, region, "ps")
        self.assertEqual((a, cached.render_calls), (b, 1))
        self.assertEqual(cached.revision, profile.revision)
        self.assertTrue(cached.region_level)


# ------------------------------------------------------------------------------------ CLI and report


class CliTest(FilesFixture):
    def main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = vp.main(argv)
        return code, out.getvalue(), err.getvalue()

    def argv(self, *extra, src=None):
        return [self.appid, src or self.src, "--quick", "--profiles", self.factory, "--profiles-arg", self.arg] + list(extra)

    def test_exit_zero_json_shape_and_deterministic_report(self):
        paths = []
        for i in range(2):
            path = os.path.join(self.base, "report%d.json" % i)
            code, out, err = self.main(self.argv("--only", "pinned,cycle,unknown-build", "--json", path))
            self.assertEqual(code, 0, (out, err))
            self.assertIn("RESULT: PASS", out)
            paths.append(path)
        reports = []
        for path in paths:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.assertEqual(set(data), {"appid", "generated_at", "kept", "kind", "name", "ok", "probes", "revision", "styles"})
            for p in data["probes"]:
                self.assertEqual(set(p), {"name", "status", "number", "seconds", "details", "data"})
            data.pop("generated_at")
            for p in data["probes"]:
                p.pop("seconds")
            reports.append(json.dumps(data, sort_keys=True))
        self.assertEqual(reports[0], reports[1])
        self.assertEqual(json.loads(reports[0])["kind"], "files")

    def test_exit_codes(self):
        self.assertEqual(self.main(["99999", self.src])[0], 2)                      # no such profile
        code, _, err = self.main(self.argv("--only", "nope"))
        self.assertEqual(code, 2)
        self.assertIn("unknown probe", err)
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, True)
        code, _, err = self.main(self.argv("--only", "pinned", src=empty))
        self.assertEqual(code, 2)
        self.assertIn("missing in", err)
        with mock.patch.object(files_patch, "revert", lambda *a, **k: None):
            code, out, _ = self.main(self.argv("--only", "cycle"))
        self.assertEqual(code, 1)
        self.assertIn("RESULT: FAIL", out)

    def test_nothing_is_written_next_to_the_users_copies_and_keep_keeps(self):
        before = vw.tree_of(self.src)
        code, out, _ = self.main(self.argv("--only", "cycle", "--keep"))
        self.assertEqual(code, 0)
        self.assertEqual(vw.tree_of(self.src), before)
        kept = out.split("work folder kept at ")[1].strip()
        self.assertTrue(os.path.isdir(kept))
        shutil.rmtree(kept)

    def test_a_probe_that_raises_is_a_failed_probe_not_a_pass(self):
        with mock.patch.dict(vp.FUNCS, {"cycle": lambda ctx: 1 / 0}):
            rep = self.run_probes(["cycle"])
        self.assertEqual(rep["probes"][0]["status"], "FAIL")
        self.assertIn("ZeroDivisionError", rep["probes"][0]["details"][0])
        self.assertFalse(rep["ok"])

    def test_catalog_probe_reports_the_build_check(self):
        for rc, status in ((0, "PASS"), (1, "FAIL")):
            done = mock.Mock(returncode=rc, stdout="stale" if rc else "ok", stderr="")
            with mock.patch.object(vp.subprocess, "run", return_value=done):
                rep = self.run_probes(["catalog"])
            self.assertEqual(rep["probes"][0]["status"], status)

    def test_visual_probe_skips_without_pillow(self):
        with mock.patch.dict(sys.modules, {"PIL": None}):
            rep = self.run_probes(["visual"], sheet=os.path.join(self.base, "sheets"))
        self.assertEqual(rep["probes"][0]["status"], "SKIP")
        self.assertIn("Pillow", rep["probes"][0]["details"][0])


if __name__ == "__main__":
    unittest.main()
