"""Entry-level patching survives a power cut at every write, with no manual step.

The forge entry strategy moves four things: the trailer, the new blob, the index slot and the size kept
in the entry's name record. The slot and the name size are two separate writes (the index and the name
table are far apart), so a cut between them used to leave a file that nothing noticed. These tests cut
at EVERY write of an apply and of an Original, and check the recovery. See docs/games/15100-assassins-creed.md.
"""
import os
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("py_modules", "tests", "tools"):
    sys.path.insert(0, os.path.join(ROOT, sub))

from button_glyphs import engine, entries  # noqa: E402
from button_glyphs.forge import ALIGN, FILEDATA_HEADER, append_blob, set_slot  # noqa: E402
from button_glyphs.trailer import write_trailer  # noqa: E402
import test_entries as te  # noqa: E402
from test_backend import GLYPH_DIR, make_library  # noqa: E402
from verify_worlds import Crash, CrashInjector  # noqa: E402

APPID = "15100"
FILES = (te.PRIMARY, te.AREA)
RECOGNISED = (entries.ORIGINAL, entries.PARTIAL, entries.HALF, entries.PATCHED)


def rd(path):
    with open(path, "rb") as f:
        return f.read()


_FORGES = {}


def forge_bytes():
    if not _FORGES:
        d = tempfile.mkdtemp()
        try:
            te.fx.build_forge(os.path.join(d, "a"))
            te.fx.build_forge(os.path.join(d, "b"), entry=te.AREA_ENTRY)
            _FORGES[te.PRIMARY], _FORGES[te.AREA] = rd(os.path.join(d, "a")), rd(os.path.join(d, "b"))
        finally:
            shutil.rmtree(d)
    return _FORGES


class World:
    """A fake Steam library with the two synthetic forges, an engine, and helpers to look at the files."""

    def __init__(self, testcase):
        self.home = tempfile.mkdtemp()
        testcase.addCleanup(shutil.rmtree, self.home, True)
        common = make_library(self.home, [(APPID, "Assassin's Creed", "Assassins Creed", "4")])
        self.dir = os.path.join(common, "Assassins Creed")
        self.path = {n: os.path.join(self.dir, n) for n in FILES}
        for name, content in forge_bytes().items():     # built once: the LZO fixture is slow
            with open(self.path[name], "wb") as f:
                f.write(content)
        self.start = self.shas()
        os.makedirs(os.path.join(self.home, "proc"))
        self.eng = engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={APPID: te.make_profile()}, proc_root=os.path.join(self.home, "proc"))

    def shas(self):
        return {n: te.sha(p) for n, p in self.path.items()}

    def states(self):
        return [entries.inspect(self.path[n])[0] for n in FILES]

    def status(self):
        """What the user would see after a REAL power cut: no error was recorded (nothing ran to record it)."""
        cfg = self.eng.load_config()
        cfg["games"].get(APPID, {}).pop("error", None)
        self.eng.save_config(cfg)
        return next(g for g in self.eng.list_games() if g["appid"] == APPID)

    def fields(self, name):
        """(slot, name size) of the entry the trailer describes, read from the file."""
        _, trailer = entries.inspect(self.path[name])
        e = trailer["entries"][0]
        with open(self.path[name], "rb") as f:
            return entries._read_slot(f, e["slot_off"]), entries._read_name_size(f, e["name_off"]), e

    def crash(self, style, k):
        try:
            with CrashInjector([self.dir], k=k):
                self.eng.set_style(APPID, style)
        except Crash:
            pass

    def count_writes(self, style):
        with CrashInjector([self.dir]) as inj:
            self.eng.set_style(APPID, style)
        return inj.count


def assert_whole(testcase, world, style):
    """Every field of every file agrees: slot and name size both new (or both original), size per trailer."""
    for name in FILES:
        state, trailer = entries.inspect(world.path[name])
        if style == "original":
            testcase.assertEqual(state, entries.ORIGINAL, name)
            continue
        testcase.assertEqual(state, entries.PATCHED, name)
        slot, name_size, e = world.fields(name)
        testcase.assertEqual(slot, (e["new"][0], e["orig"][1], e["new"][1]))
        testcase.assertEqual(name_size, e["new"][1])
        testcase.assertEqual(os.path.getsize(world.path[name]), trailer["patched_size"])


class EveryCutTest(unittest.TestCase):
    def test_apply_cut_at_every_write_recovers(self):
        w = World(self)
        n = w.count_writes("ps")
        self.assertGreater(n, 8)
        seen = set()
        for k in range(n):
            w = World(self)
            w.crash("ps", k)
            states = w.states()
            seen.update(states)
            self.assertTrue(set(states) <= set(RECOGNISED), (k, states))
            for name, state in zip(FILES, w.states()):
                if state == entries.ORIGINAL:
                    self.assertEqual(te.sha(w.path[name]), w.start[name], (k, name))   # nothing written yet
            self.assertNotEqual(w.status()["state"], "ok", (k, states))
            self.assertTrue(w.eng.sync(APPID)["ok"], k)
            assert_whole(self, w, "ps")
            self.assertEqual(w.status()["state"], "ok", k)
            self.assertTrue(w.eng.set_style(APPID, "original")["ok"])
            self.assertEqual(w.shas(), w.start, k)
        # the cuts really visited the windows this file is about
        self.assertTrue({entries.PARTIAL, entries.HALF} <= seen, seen)

    def test_original_cut_at_every_write_recovers(self):
        w = World(self)
        w.eng.set_style(APPID, "ps")
        n = w.count_writes("original")
        self.assertGreater(n, 4)
        seen = set()
        for k in range(n):
            w = World(self)
            w.eng.set_style(APPID, "ps")
            patched = w.shas()
            w.crash("original", k)
            states = w.states()
            seen.update(states)
            self.assertTrue(set(states) <= set(RECOGNISED), (k, states))
            for name, state in zip(FILES, states):
                if state == entries.PATCHED:
                    self.assertEqual(te.sha(w.path[name]), patched[name], (k, name))
            self.assertNotEqual(w.status()["state"], "ok", (k, states))   # Original was chosen, not finished
            self.assertTrue(w.eng.sync(APPID)["ok"], k)
            self.assertEqual(w.shas(), w.start, k)    # exact bytes, whole-file SHA-256
            self.assertEqual(w.status()["state"], "ok", k)
        self.assertTrue({entries.PARTIAL, entries.HALF} <= seen, seen)

    def test_original_cut_then_the_player_picks_a_style_again(self):
        for k in (1, 2, 4):   # half (slot back, name size not), partial, and the second file half-way
            w = World(self)
            w.eng.set_style(APPID, "ps")
            w.crash("original", k)
            self.assertTrue(w.eng.set_style(APPID, "xbox")["ok"], k)
            self.assertEqual(w.status()["state"], "ok", k)
            self.assertTrue(w.eng.set_style(APPID, "original")["ok"])
            self.assertEqual(w.shas(), w.start, k)

    def test_a_second_cut_while_recovering_still_converges(self):
        # cut the apply right between slot and name size (the window), then cut the repair at every write
        w = World(self)
        w.crash("ps", 4)
        self.assertIn(entries.HALF, w.states())
        with CrashInjector([w.dir]) as inj:
            w.eng.sync(APPID)
        for k in range(inj.count):
            w = World(self)
            w.crash("ps", 4)
            w.crash("ps", k)
            self.assertTrue(set(w.states()) <= set(RECOGNISED), (k, w.states()))
            self.assertTrue(w.eng.sync(APPID)["ok"], k)
            assert_whole(self, w, "ps")
            w.eng.set_style(APPID, "original")
            self.assertEqual(w.shas(), w.start, k)


class TheTwoWindowsTest(unittest.TestCase):
    """The exact windows of the bug, built by hand (the every-k tests above reach them through the writer)."""

    def patched_world(self):
        w = World(self)
        self.assertTrue(w.eng.set_style(APPID, "ps")["ok"])
        return w

    def test_apply_cut_after_the_slot_before_the_name_size(self):
        w = self.patched_world()
        slot, name_size, e = w.fields(te.AREA)
        self.assertNotEqual(e["orig_name_size"], e["new"][1])      # the fixture really tells the two apart
        with open(w.path[te.AREA], "r+b") as f:
            entries._set_name_size(f, e, e["orig_name_size"])       # what is on disk after that cut
        self.assertEqual(entries.inspect(w.path[te.AREA])[0], entries.HALF)
        self.assertEqual(w.status()["state"], "pending")
        res = w.eng.sync(APPID)
        self.assertTrue(res["ok"] and res["changed"], res)
        assert_whole(self, w, "ps")
        self.assertEqual(w.status()["state"], "ok")

    def test_apply_cut_after_the_slot_is_repaired_by_original_too(self):
        w = self.patched_world()
        _, _, e = w.fields(te.AREA)
        with open(w.path[te.AREA], "r+b") as f:
            entries._set_name_size(f, e, e["orig_name_size"])
        self.assertTrue(w.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(w.shas(), w.start)

    def test_original_cut_after_the_slot_before_the_name_size(self):
        w = self.patched_world()
        _, _, e = w.fields(te.AREA)
        with open(w.path[te.AREA], "r+b") as f:
            entries._set_slot(f, e, e["orig"][0], e["orig"][2])     # slot back, name size still the new one
        self.assertEqual(entries.inspect(w.path[te.AREA])[0], entries.HALF)   # was PARTIAL: nothing saw it
        state, trailer = entries.inspect(w.path[te.AREA])
        self.assertNotEqual(w.status()["state"], "ok")
        entries.revert(w.path[te.AREA], state, trailer)
        entries.revert(w.path[te.PRIMARY], *entries.inspect(w.path[te.PRIMARY]))
        self.assertEqual(w.shas(), w.start)

    def test_every_combination_of_the_two_fields_is_one_state(self):
        for slot_new in (False, True):
            for name_new in (False, True):
                w = self.patched_world()
                _, _, e = w.fields(te.AREA)
                with open(w.path[te.AREA], "r+b") as f:
                    if not slot_new:
                        entries._set_slot(f, e, e["orig"][0], e["orig"][2])
                    if not name_new:
                        entries._set_name_size(f, e, e["orig_name_size"])
                want = (entries.PATCHED if slot_new and name_new
                        else entries.PARTIAL if not slot_new and not name_new else entries.HALF)
                self.assertEqual(entries.inspect(w.path[te.AREA])[0], want, (slot_new, name_new))
                self.assertTrue(w.eng.set_style(APPID, "original")["ok"])
                self.assertEqual(w.shas(), w.start, (slot_new, name_new))

    def test_revert_is_idempotent(self):
        w = self.patched_world()
        state, trailer = entries.inspect(w.path[te.AREA])
        entries.revert(w.path[te.AREA], state, trailer)
        self.assertEqual(te.sha(w.path[te.AREA]), w.start[te.AREA])
        # a second run after the cut that comes right before the truncate: fields original, trailer still there
        w = self.patched_world()
        state, trailer = entries.inspect(w.path[te.AREA])
        e = trailer["entries"][0]
        with open(w.path[te.AREA], "r+b") as f:
            entries._set_slot(f, e, e["orig"][0], e["orig"][2])
            entries._set_name_size(f, e, e["orig_name_size"])
        state, trailer = entries.inspect(w.path[te.AREA])
        self.assertEqual(state, entries.PARTIAL)
        entries.revert(w.path[te.AREA], state, trailer)
        self.assertEqual(te.sha(w.path[te.AREA]), w.start[te.AREA])


class DurabilityOrderTest(unittest.TestCase):
    """The slot is durable (fsync) before the name size is written, in apply and in Original."""

    class Log(CrashInjector):
        def __init__(self, roots):
            super().__init__(roots)
            self.events = []

        def event(self, kind, data=None, handle=None):
            self.events.append((kind, len(data) if data is not None else None))

    def log_of(self, action):
        w = World(self)
        if action == "original":
            w.eng.set_style(APPID, "ps")
        log = self.Log([w.dir])
        real_fsync = os.fsync

        def fsync(fd):
            log.events.append(("fsync", None))
            return real_fsync(fd)

        with log, mock.patch.object(os, "fsync", fsync):
            w.eng.set_style(APPID, "ps" if action == "apply" else "original")
        return log.events

    def assert_fsync_between(self, events):
        slots = [i for i, e in enumerate(events) if e == ("write", entries.SLOT.size)]
        names = [i for i, e in enumerate(events) if e == ("write", 4)]
        self.assertEqual(len(slots), 2)           # one per file
        self.assertEqual(len(names), 2)
        for s_, n_ in zip(slots, names):
            self.assertLess(s_, n_)
            self.assertIn(("fsync", None), events[s_ + 1:n_])

    def test_apply(self):
        self.assert_fsync_between(self.log_of("apply"))

    def test_original(self):
        self.assert_fsync_between(self.log_of("original"))


class TrailerPlacementTest(unittest.TestCase):
    def test_write_trailer_at_refuses_a_bad_offset_and_writes_nothing(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "f.bin")
        with open(path, "wb") as f:
            f.write(b"x" * (3 * ALIGN))
        before = rd(path)
        for at in (ALIGN, 2 * ALIGN, 3 * ALIGN - 1, 3 * ALIGN + 1, 5 * ALIGN + 7):
            with self.assertRaises(ValueError, msg=at):
                write_trailer(path, {"a": 1}, at=at)
            self.assertEqual(rd(path), before)
        write_trailer(path, {"a": 1}, at=3 * ALIGN)            # at the end: allowed
        write_trailer(path, {"a": 1}, at=6 * ALIGN)            # beyond it (the gap reads as zeros): allowed
        self.assertEqual(len(rd(path)), 7 * ALIGN)
        self.assertEqual(rd(path)[3 * ALIGN + ALIGN:6 * ALIGN], b"\0" * (2 * ALIGN))


class OldFilesTest(unittest.TestCase):
    def old_apply(self, src, dst, record, header_payload):
        """The previous writer's order and calls (blob -> trailer -> slot+name size), on a copy of the original."""
        shutil.copyfile(src, dst)
        e = record["entries"][0]
        with open(dst, "r+b") as f:
            off = append_blob(f, header_payload[:FILEDATA_HEADER], header_payload[FILEDATA_HEADER:])
            f.flush()
        self.assertEqual(off, e["new"][0])
        write_trailer(dst, record)
        with open(dst, "r+b") as f:
            set_slot(f, te.slot_of(e), e["new"][0], e["new"][1], e["new"][1])

    def test_new_writer_makes_the_same_bytes_and_old_files_read_patched(self):
        w = World(self)
        orig = os.path.join(w.home, "orig.forge")
        shutil.copyfile(w.path[te.AREA], orig)
        self.assertTrue(w.eng.set_style(APPID, "ps")["ok"])
        new_bytes = rd(w.path[te.AREA])
        _, trailer = entries.inspect(w.path[te.AREA])
        e = trailer["entries"][0]
        header_payload = new_bytes[e["new"][0]:e["new"][0] + FILEDATA_HEADER + e["new"][1]]
        old = os.path.join(w.home, "old.forge")
        self.old_apply(orig, old, trailer, header_payload)
        self.assertEqual(rd(old), new_bytes)           # the on-disk format did not change
        state, tr = entries.inspect(old)
        self.assertEqual((state, tr), (entries.PATCHED, trailer))
        entries.revert(old, state, tr)
        self.assertEqual(te.sha(old), te.sha(orig))

    def test_file_the_old_writer_left_half_applied_is_repaired_not_strange(self):
        w = World(self)
        self.assertTrue(w.eng.set_style(APPID, "ps")["ok"])
        _, _, e = w.fields(te.AREA)
        with open(w.path[te.AREA], "r+b") as f:   # old cut: slot written, name size never
            f.seek(e["name_off"])
            f.write(struct.pack("<I", e["orig_name_size"]))
        self.assertEqual(entries.inspect(w.path[te.AREA])[0], entries.HALF)
        self.assertTrue(w.eng.sync(APPID)["ok"])
        assert_whole(self, w, "ps")


class StrangeStaysStrangeTest(unittest.TestCase):
    """A changed file (game update) is never repaired by guesswork: only our own recorded values qualify."""

    def strange_case(self, mutate):
        w = World(self)
        self.assertTrue(w.eng.set_style(APPID, "ps")["ok"])
        _, _, e = w.fields(te.AREA)
        with open(w.path[te.AREA], "r+b") as f:
            mutate(f, e)
        before = rd(w.path[te.AREA])
        state, trailer = entries.inspect(w.path[te.AREA])
        self.assertEqual(state, entries.STRANGE)
        entries.revert(w.path[te.AREA], state, trailer)
        after = rd(w.path[te.AREA])
        self.assertEqual(after, before[:-ALIGN])      # only our marker is cut, nothing else is written
        self.assertIsNone(engine.read_trailer(w.path[te.AREA]))

    def test_slot_not_ours(self):
        self.strange_case(lambda f, e: set_slot(f, te.slot_of(e), 12345, 1, e["new"][1]))

    def test_name_size_not_ours_with_a_live_slot(self):
        self.strange_case(lambda f, e: (f.seek(e["name_off"]), f.write(struct.pack("<I", 777))))

    def test_name_size_not_ours_with_an_original_slot(self):
        def mutate(f, e):
            entries._set_slot(f, e, e["orig"][0], e["orig"][2])
            f.seek(e["name_off"])
            f.write(struct.pack("<I", 777))
        self.strange_case(mutate)

    def test_file_longer_than_recorded_is_no_longer_ours(self):
        w = World(self)
        self.assertTrue(w.eng.set_style(APPID, "ps")["ok"])
        with open(w.path[te.AREA], "ab") as f:
            f.write(b"\0" * 16)
        self.assertEqual(entries.inspect(w.path[te.AREA])[0], entries.ORIGINAL)   # the trailer is no longer last

    def test_half_state_with_a_wrong_size_is_not_repaired(self):
        w = World(self)
        self.assertTrue(w.eng.set_style(APPID, "ps")["ok"])
        _, _, e = w.fields(te.AREA)
        _, trailer = entries.inspect(w.path[te.AREA])
        with open(w.path[te.AREA], "r+b") as f:
            entries._set_name_size(f, e, e["orig_name_size"])
        state = entries.inspect(w.path[te.AREA])[0]
        self.assertEqual(state, entries.HALF)
        # same fields, but the file is not the size we recorded (Steam changed it): not ours to repair
        with mock.patch.object(entries, "read_trailer", return_value=dict(trailer, patched_size=trailer["patched_size"] + ALIGN)):
            self.assertEqual(entries.inspect(w.path[te.AREA])[0], entries.STRANGE)


if __name__ == "__main__":
    unittest.main()
