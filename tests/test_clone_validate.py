"""tools/clone_validate.py on synthetic games, and the failure modes it must catch."""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

spec = importlib.util.spec_from_file_location("clone_validate", os.path.join(ROOT, "tools", "clone_validate.py"))
clone_validate = importlib.util.module_from_spec(spec)
sys.modules["clone_validate"] = clone_validate
spec.loader.exec_module(clone_validate)

import forge_fixture as fx  # noqa: E402
from test_ten_second_ninja_x import APPID as NINJA, BUILD as NINJA_BUILD, DATA_WIN, SAVE_REL  # noqa: E402
from test_ten_second_ninja_x import padded, save_text, test_profile as ninja_profile  # noqa: E402
from test_entries import AREA, AREA_ENTRY, PRIMARY, make_profile  # noqa: E402


class CloneValidateTest(unittest.TestCase):
    def setUp(self):
        self.src = tempfile.mkdtemp()
        fx.build_forge(os.path.join(self.src, PRIMARY))
        fx.build_forge(os.path.join(self.src, AREA), entry=AREA_ENTRY)
        self.profiles = {"15100": make_profile()}
        self.lines = []

    def tearDown(self):
        shutil.rmtree(self.src)

    def validate(self, **kw):
        return clone_validate.validate("15100", self.src, profiles=self.profiles, log=self.lines.append, **kw)

    def test_a_correct_profile_passes_the_full_cycle(self):
        report = self.validate()
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual([s["style"] for s in report["steps"]], ["ps", "xbox", "ps", "original"])
        self.assertTrue(all(s["ok"] and s["state"] == "ok" for s in report["steps"]))
        self.assertTrue(any("2/2 file(s) byte-identical" in l for l in self.lines))
        self.assertIsNone(report["kept"])

    def test_keep_leaves_the_patched_clone(self):
        report = self.validate(styles=["ps"], keep=True)
        try:
            self.assertTrue(report["ok"])
            self.assertTrue(os.path.isdir(report["kept"]))
        finally:
            shutil.rmtree(report["kept"], ignore_errors=True)

    def test_missing_files_are_reported_before_anything_runs(self):
        os.remove(os.path.join(self.src, AREA))
        report = self.validate()
        self.assertFalse(report["ok"])
        self.assertIn(AREA, report["problems"][0])
        self.assertEqual(report["steps"], [])

    def test_unknown_appid_and_unsupported_layout(self):
        self.assertFalse(clone_validate.validate("1", self.src, profiles=self.profiles, log=print)["ok"])
        with open(os.path.join(self.src, PRIMARY), "wb") as f:
            f.write(b"scimitar\0" + b"\0" * 100)
        report = self.validate()
        self.assertFalse(report["ok"])
        self.assertTrue(any("not supported" in p for p in report["problems"]))

    def test_a_broken_undo_is_detected(self):
        # sabotage: make the engine's "original" leave a trailing byte behind
        from button_glyphs import engine
        real = engine.Engine.sync

        def sloppy(self, appid, reason="check"):
            result = real(self, appid, reason)
            if self.load_config()["games"][appid]["style"] == "original":
                app = __import__("button_glyphs.steam", fromlist=["x"]).installed_apps(self.home)[appid]
                with open(os.path.join(app["install_path"], PRIMARY), "ab") as f:
                    f.write(b"\0")
            return result

        engine.Engine.sync = sloppy
        try:
            report = self.validate()
        finally:
            engine.Engine.sync = real
        self.assertFalse(report["ok"])
        self.assertTrue(any("NOT identical" in p for p in report["problems"]))

    def test_cli_exit_codes(self):
        out = io.StringIO()
        old, sys.stdout = sys.stdout, out
        try:
            self.assertEqual(clone_validate.main(["99999", self.src]), 1)
        finally:
            sys.stdout = old
        self.assertIn("FAIL", out.getvalue())


class CloneValidateConfigValueTest(unittest.TestCase):
    """A config-value game: data.win in the install folder, the save inside the Proton prefix."""

    def setUp(self):
        self.src = tempfile.mkdtemp()
        self.save = os.path.join(self.src, SAVE_REL)
        os.makedirs(os.path.dirname(self.save))
        with open(os.path.join(self.src, "data.win"), "wb") as f:
            f.write(DATA_WIN)
        self.write_save(padded(save_text("525.000000")))
        self.profiles = {NINJA: ninja_profile()}
        self.lines = []

    def tearDown(self):
        shutil.rmtree(self.src)

    def write_save(self, data):
        with open(self.save, "wb") as f:
            f.write(data)

    def validate(self, **kw):
        return clone_validate.validate(NINJA, self.src, profiles=self.profiles, log=self.lines.append, **kw)

    def test_full_cycle_gives_back_the_pristine_save(self):
        with open(self.save, "rb") as f:
            pristine = f.read()
        report = self.validate()
        self.assertTrue(report["ok"], report["problems"])
        self.assertEqual([s["style"] for s in report["steps"]], ["ps", "xbox", "ps", "original"])
        self.assertTrue(all(s["ok"] and s["state"] == "ok" for s in report["steps"]))
        self.assertTrue(any("2/2 file(s) byte-identical" in l for l in self.lines))
        with open(self.save, "rb") as f:                # the source copy itself is never written
            self.assertEqual(f.read(), pristine)

    def test_missing_save_or_unpinned_build_is_reported(self):
        os.remove(self.save)
        report = self.validate()
        self.assertFalse(report["ok"])
        self.assertIn(SAVE_REL, report["problems"][0])
        self.write_save(padded(save_text()))
        report = self.validate(buildid="1")
        self.assertFalse(report["ok"])
        self.assertIn("not pinned", report["problems"][0])
        self.assertTrue(self.validate(buildid=NINJA_BUILD)["ok"])

    def test_a_broken_undo_of_the_save_is_detected(self):
        from button_glyphs import engine
        real = engine.Engine.sync

        def sloppy(self_, appid, reason="check"):
            result = real(self_, appid, reason)
            if self_.load_config()["games"][appid]["style"] == "original":
                path = os.path.join(self_.home, ".local/share/Steam/steamapps/compatdata", appid, "pfx", SAVE_REL)
                with open(path, "r+b") as f:
                    f.seek(5)
                    f.write(b"#")
            return result
        engine.Engine.sync = sloppy
        try:
            report = self.validate()
        finally:
            engine.Engine.sync = real
        self.assertFalse(report["ok"])
        self.assertTrue(any("NOT identical" in p for p in report["problems"]))


if __name__ == "__main__":
    unittest.main()
