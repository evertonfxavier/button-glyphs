"""tools/scaffold_profile.py: a new game starts from a valid, empty, consistent skeleton."""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

spec = importlib.util.spec_from_file_location("scaffold_profile", os.path.join(ROOT, "tools", "scaffold_profile.py"))
scaffold_profile = importlib.util.module_from_spec(spec)
sys.modules["scaffold_profile"] = scaffold_profile
spec.loader.exec_module(scaffold_profile)

import button_glyphs.profiles  # noqa: E402,F401  (the generated modules import from this package)


def load_profile(path, slug):
    """Import a generated profile as if it lived in button_glyphs.profiles."""
    name = "button_glyphs.profiles._scaffold_" + slug
    s = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(s)
    sys.modules[name] = module
    s.loader.exec_module(module)
    return module


class ScaffoldTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs", "games"))
        shutil.copy(os.path.join(ROOT, "docs", "games", "_template.md"),
                    os.path.join(self.root, "docs", "games", "_template.md"))

    def tearDown(self):
        shutil.rmtree(self.root)
        for name in [n for n in sys.modules if n.startswith("button_glyphs.profiles._scaffold_")]:
            del sys.modules[name]

    def test_every_kind_produces_importable_skeleton_files(self):
        for kind in sorted(scaffold_profile.PROFILE):
            slug = "demo_" + kind.replace("-", "_")
            files, registry = scaffold_profile.scaffold(self.root, "424242", "Demo Game", kind, slug=slug)
            self.assertEqual(len(files), 3, kind)
            profile_py, doc_md, test_py = (p for p, _ in files)
            for generated in (profile_py, test_py):
                compile(generated.read_text(), str(generated), "exec")  # syntax only, writes nothing
            module = load_profile(str(profile_py), slug)
            self.assertEqual(module.PROFILE.appid, "424242", kind)
            self.assertEqual(module.PROFILE.name, "Demo Game")
            self.assertTrue(module.PROFILE.roles)
            self.assertEqual(module.PROFILE.revision, 1)
            doc = doc_md.read_text()
            self.assertIn("# Demo Game (424242)", doc)
            self.assertIn("## Learnings", doc)
            self.assertIn(kind, doc)
            self.assertEqual(registry[1], slug.upper())

    def test_refuses_to_overwrite_unless_forced(self):
        scaffold_profile.scaffold(self.root, "424242", "Demo Game", "il-patch", slug="demo")
        with self.assertRaises(FileExistsError):
            scaffold_profile.scaffold(self.root, "424242", "Demo Game", "il-patch", slug="demo")
        scaffold_profile.scaffold(self.root, "424242", "Demo Game", "il-patch", slug="demo", force=True)

    def test_dry_run_writes_nothing(self):
        files, _ = scaffold_profile.scaffold(self.root, "1", "X", "dxt-regions", slug="dry", dry_run=True)
        self.assertEqual(len(files), 3)
        self.assertFalse(os.path.exists(os.path.join(self.root, "py_modules")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "tests")))

    def test_slug_and_input_validation(self):
        self.assertEqual(scaffold_profile.slugify("Saint Seiya: Soldiers' Soul"), "saint_seiya_soldiers_soul")
        with self.assertRaises(ValueError):
            scaffold_profile.scaffold(self.root, "abc", "X", "il-patch")
        with self.assertRaises(ValueError):
            scaffold_profile.scaffold(self.root, "1", "X", "nonsense")
        with self.assertRaises(ValueError):
            scaffold_profile.scaffold(self.root, "1", "X", "il-patch", slug="Bad-Slug")

    def test_scaffolded_games_are_not_registered(self):
        scaffold_profile.scaffold(self.root, "424242", "Demo Game", "region-glyphs", slug="demo_reg")
        self.assertNotIn("424242", button_glyphs.profiles.PROFILES)

    def test_every_registered_game_has_a_catalog_page(self):
        pages = os.listdir(os.path.join(ROOT, "docs", "games"))
        for appid in button_glyphs.profiles.PROFILES:
            self.assertTrue(any(p.startswith(appid + "-") for p in pages), "docs/games/%s-*.md missing" % appid)


if __name__ == "__main__":
    unittest.main()
