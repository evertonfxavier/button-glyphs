"""The watcher (Engine.sync_all) finishes an Original that was cut, for entry, region and single-file games.

A power cut inside Original (or the game running when Original was chosen) leaves a file that still carries OUR
marker while the config already says Original. The watcher used to skip such games for ever; it now syncs them,
but only when a file is in a state we wrote (never for unknown / strange files), never while the game runs, and an
idle tick writes nothing.
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("py_modules", "tests", "tools"):
    sys.path.insert(0, os.path.join(ROOT, sub))

from button_glyphs import engine, entries, region_patch  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402
from button_glyphs.profiles.region_glyphs import RegionGlyphProfile  # noqa: E402
import forge_fixture as fx  # noqa: E402
import test_entries as te  # noqa: E402
import test_regions as trg  # noqa: E402
from test_backend import GLYPH_DIR, fake_charlie_exe, make_library  # noqa: E402
from verify_worlds import Crash, CrashInjector  # noqa: E402


def tree(root):
    """Every file under `root` -> SHA-256 (a stray temp file shows)."""
    out = {}
    for d, _, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


class Base:
    """Subclasses build `self.install`, `self.appid`, `self.profile` and implement the kind-specific bits."""
    appid = profile = None
    app = ("Game", "Game")

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home, True)
        common = make_library(self.home, [(self.appid, self.app[0], self.app[1], "4")])
        self.install = os.path.join(common, self.app[1])
        self.proc = os.path.join(self.home, "proc")
        os.makedirs(self.proc)
        self.build()
        self.start = tree(self.install)
        self.eng = engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={self.appid: self.profile}, proc_root=self.proc)

    def cut_original(self, k):
        self.assertTrue(self.eng.set_style(self.appid, "ps")["ok"])
        try:
            with CrashInjector([self.install], k=k):
                self.eng.set_style(self.appid, "original")
        except Crash:
            pass
        # a real power cut records nothing: forget the error the engine noted for the exception
        cfg = self.eng.load_config()
        cfg["games"][self.appid].pop("error", None)
        self.eng.save_config(cfg)

    def count_original_writes(self):
        self.eng.set_style(self.appid, "ps")
        with CrashInjector([self.install]) as inj:
            self.eng.set_style(self.appid, "original")
        self.eng.set_style(self.appid, "ps")
        return inj.count

    def status(self):
        return next(g for g in self.eng.list_games() if g["appid"] == self.appid)

    def test_one_watcher_tick_finishes_a_cut_original(self):
        n = self.count_original_writes()
        self.assertGreater(n, 0)
        cut_marked = 0
        for k in range(n):
            self.tearDown_world()
            self.setUp()
            self.cut_original(k)
            marked = tree(self.install) != self.start
            cut_marked += marked
            if marked:
                self.assertNotEqual(self.status()["state"], "ok", k)     # never "ok" while half-done
            results = self.eng.sync_all("watch")
            self.assertEqual(tree(self.install), self.start, k)          # exact original, whole tree
            self.assertEqual(self.status()["state"], "ok", k)
            self.assertEqual(len(results), 1 if marked else 0, k)       # a clean file is not visited
        self.assertGreater(cut_marked, 0)                                # the cuts really left something to finish

    def test_an_idle_tick_writes_nothing(self):
        self.assertTrue(self.eng.set_style(self.appid, "ps")["ok"])
        self.assertTrue(self.eng.set_style(self.appid, "original")["ok"])
        before = (tree(self.install), self.config_bytes())
        mtime = os.stat(self.eng.config_path).st_mtime_ns
        with CrashInjector([self.install, self.home]) as inj:
            self.assertEqual(self.eng.sync_all("watch"), [])
        self.assertEqual(inj.count, 0)
        self.assertEqual((tree(self.install), self.config_bytes()), before)
        self.assertEqual(os.stat(self.eng.config_path).st_mtime_ns, mtime)

    def config_bytes(self):
        with open(self.eng.config_path, "rb") as f:
            return f.read()

    def test_a_file_that_is_not_ours_never_triggers_a_sync(self):
        self.assertTrue(self.eng.set_style(self.appid, "ps")["ok"])
        self.assertTrue(self.eng.set_style(self.appid, "original")["ok"])
        self.make_unknown()
        before = tree(self.install)
        with mock.patch.object(engine.Engine, "sync", side_effect=AssertionError("synced")):
            self.assertEqual(self.eng.sync_all("watch"), [])
        self.assertEqual(tree(self.install), before)

    def test_a_running_game_defers_the_restore(self):
        self.cut_original(0)
        cut = tree(self.install)
        self.assertNotEqual(cut, self.start)
        os.makedirs(os.path.join(self.proc, "4242"))
        with open(os.path.join(self.proc, "4242", "cmdline"), "wb") as f:
            f.write(self.install.encode() + b"/Game.exe\0")
        with CrashInjector([self.install]) as inj:
            results = self.eng.sync_all("watch")
        self.assertEqual(inj.count, 0)
        self.assertEqual(tree(self.install), cut)
        self.assertEqual([r["changed"] for r in results], [False])
        self.assertEqual(self.status()["state"], "running")
        shutil.rmtree(os.path.join(self.proc, "4242"))
        self.eng.sync_all("watch")                                        # the game closed
        self.assertEqual(tree(self.install), self.start)

    def test_an_updating_game_is_left_alone(self):
        self.cut_original(0)
        cut = tree(self.install)
        make_library(self.home, [(self.appid, self.app[0], self.app[1], "1030")])      # Steam is updating it
        self.eng.sync_all("watch")
        self.assertEqual(tree(self.install), cut)
        make_library(self.home, [(self.appid, self.app[0], self.app[1], "4")])
        self.eng.sync_all("watch")
        self.assertEqual(tree(self.install), self.start)

    def test_a_style_game_is_synced_as_before(self):
        self.assertTrue(self.eng.set_style(self.appid, "ps")["ok"])
        self.assertEqual([r["changed"] for r in self.eng.sync_all("watch")], [False])   # still visited, nothing to do

    def tearDown_world(self):
        shutil.rmtree(self.home, ignore_errors=True)


class EntriesWatcherTest(Base, unittest.TestCase):
    appid = "15100"
    app = ("Assassin's Creed", "Assassins Creed")

    def build(self):
        self.profile = te.make_profile()
        fx.build_forge(os.path.join(self.install, te.PRIMARY))
        fx.build_forge(os.path.join(self.install, te.AREA), entry=te.AREA_ENTRY)

    def make_unknown(self):
        # Steam replaced the files with another build: no marker at all
        fx.build_forge(os.path.join(self.install, te.PRIMARY), variant=1)

    def test_a_strange_file_never_triggers_a_sync(self):
        self.assertTrue(self.eng.set_style(self.appid, "ps")["ok"])
        path = os.path.join(self.install, te.AREA)
        state, trailer = entries.inspect(path)
        e = trailer["entries"][0]
        with open(path, "r+b") as f:
            entries._set_slot(f, e, 12345, 1)
        self.assertEqual(entries.inspect(path)[0], entries.STRANGE)
        cfg = self.eng.load_config()
        cfg["games"][self.appid]["style"] = "original"
        self.eng.save_config(cfg)
        # the primary file is still patched (ours), so this game does sync: the strange one is only unmarked
        self.assertEqual(len(self.eng.sync_all("watch")), 1)
        self.assertEqual(self.eng.sync_all("watch"), [])                 # and then it is quiet: no loop


class RegionsWatcherTest(Base, unittest.TestCase):
    appid = "474960"
    app = ("Quantum Break", "QuantumBreak")

    def build(self):
        os.makedirs(os.path.join(self.install, "data"))
        textures = [trg.texture(7), trg.texture(9)]
        regions, end = trg.make_regions(textures)
        self.profile = RegionGlyphProfile(
            appid=self.appid, name="Quantum Break", file=trg.ARCHIVE, regions=regions,
            roles=[("a", "A / X")], native_style="xbox", revision=1)
        blob = bytearray(os.urandom(end + 5000))
        for r, t in zip(regions, textures):
            blob[r["offset"]:r["offset"] + len(t)] = t
        self.path = os.path.join(self.install, trg.ARCHIVE)
        with open(self.path, "wb") as f:
            f.write(blob)

    def make_unknown(self):
        # another build: the region bytes differ and carry no marker
        r = self.profile.regions[0]
        with open(self.path, "r+b") as f:
            f.seek(r["offset"] + 500)
            f.write(b"\xaa" * 16)
        with open(self.path, "rb") as f:
            self.assertEqual(region_patch.region_state(f, r)[0], region_patch.ORIGINAL)


class SingleFileWatcherTest(Base, unittest.TestCase):
    appid = "405290"
    app = ("Charlie Murder", "Charlie Murder")

    def build(self):
        self.profile = PROFILES[self.appid]
        fake_charlie_exe(os.path.join(self.install, self.profile.file))

    def make_unknown(self):
        fake_charlie_exe(os.path.join(self.install, self.profile.file), variant=5)

    def test_the_cut_leaves_the_trailer_and_a_tick_removes_it(self):
        self.cut_original(0)
        self.assertIsNotNone(engine.read_trailer(os.path.join(self.install, self.profile.file)))
        self.eng.sync_all("watch")
        self.assertIsNone(engine.read_trailer(os.path.join(self.install, self.profile.file)))


if __name__ == "__main__":
    unittest.main()
