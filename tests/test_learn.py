"""tools/learn.py: the feedback loop that keeps CLAUDE.md and the game pages up to date."""
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location("learn", os.path.join(ROOT, "tools", "learn.py"))
learn = importlib.util.module_from_spec(spec)
sys.modules["learn"] = learn
spec.loader.exec_module(learn)

TEMPLATE = """# Guide

## Lessons
<!-- LESSONS:START (managed by tools/learn.py) -->

<!-- LESSONS:END -->

Footer that must survive.
"""


class LearnTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "CLAUDE.md"), "w") as f:
            f.write(TEMPLATE)
        os.makedirs(os.path.join(self.dir, "docs", "games"))
        with open(os.path.join(self.dir, "docs", "games", "15100-test.md"), "w") as f:
            f.write("# Test game\n\n## Status\nok\n")
        self.state = os.path.join(self.dir, "state")
        self.lessons = learn.Lessons(self.dir, self.state)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def read(self, name="CLAUDE.md"):
        with open(os.path.join(self.dir, name)) as f:
            return f.read()

    def test_add_places_the_lesson_under_its_category_and_keeps_the_rest(self):
        self.lessons.add("Decode format code four as DXT3 not five.", "formats", source="AC1")
        self.lessons.add("Reconnect ssh before every command.", "deck")
        text = self.read()
        self.assertLess(text.index("### Formats & reverse engineering"), text.index("### Steam Deck operations"))
        self.assertIn("DXT3", text)
        self.assertTrue(text.startswith("# Guide"))
        self.assertTrue(text.rstrip().endswith("Footer that must survive."))
        self.assertEqual(self.lessons.check(), ([], 2))

    def test_duplicates_are_not_added(self):
        self.lessons.add("Rows of TGA textures are stored bottom-up so flip the icons.", "formats")
        result = self.lessons.add("TGA textures store rows bottom-up, so flip the icons.", "formats")
        self.assertIn("Already known", result)
        self.assertEqual(self.lessons.count(), 1)

    def test_different_lessons_are_both_kept(self):
        self.lessons.add("Flip the icons for bottom-up textures.", "formats")
        self.lessons.add("Reconnect the ssh master before running anything.", "deck")
        self.assertEqual(self.lessons.count(), 2)

    def test_hard_limit_asks_for_consolidation(self):
        data, _ = self.lessons.parse()
        data["design"] = ["- lesson number %d about unrelated topic %s _(x)_" % (i, "abcdefghij"[i % 10] * 5)
                          for i in range(learn.HARD_LIMIT)]
        self.lessons._write(data)
        with self.assertRaises(learn.LearnError) as ctx:
            self.lessons.add("One more completely new idea about patches.", "process")
        self.assertIn("Consolidate", str(ctx.exception))
        self.lessons.add("One more completely new idea about patches.", "process", force=True)

    def test_rejects_long_text_empty_text_and_unknown_category(self):
        with self.assertRaises(learn.LearnError):
            self.lessons.add("x" * (learn.MAX_TEXT + 1), "process")
        with self.assertRaises(learn.LearnError):
            self.lessons.add("   ", "process")
        with self.assertRaises(learn.LearnError):
            self.lessons.add("something useful here", "nonsense")

    def test_game_scope_goes_to_the_game_page(self):
        msg = self.lessons.add("Back texture is a plain disc, use unknown.", "formats", scope="game", appid="15100")
        self.assertIn("15100-test.md", msg)
        page = self.read("docs/games/15100-test.md")
        self.assertIn("## Learnings", page)
        self.assertIn("Back texture", page)
        self.assertEqual(self.lessons.count(), 0)  # nothing leaked into CLAUDE.md
        again = self.lessons.add("Back texture is a plain disc, use unknown.", "formats", scope="game", appid="15100")
        self.assertIn("Already in", again)
        with self.assertRaises(learn.LearnError):
            self.lessons.add("Whatever", "formats", scope="game", appid="99999")
        with self.assertRaises(learn.LearnError):
            self.lessons.add("Whatever", "formats", scope="game")

    def test_check_flags_hand_edits(self):
        self.lessons.add("A proper lesson about the engine ordering.", "design")
        text = self.read().replace("### Design & safety", "### Design & safety\nfree text, not a bullet")
        with open(os.path.join(self.dir, "CLAUDE.md"), "w") as f:
            f.write(text)
        problems, _ = self.lessons.check()
        self.assertTrue(any("not a bullet" in p for p in problems))
        with self.assertRaises(learn.LearnError):
            self.lessons.add("Another lesson about something else entirely.", "design")

    def test_replace_and_remove_need_a_unique_match(self):
        self.lessons.add("Alpha lesson about the first topic entirely.", "process")
        self.lessons.add("Beta lesson regarding another subject altogether.", "process")
        with self.assertRaises(learn.LearnError):
            self.lessons.replace("lesson", "x")            # matches both
        with self.assertRaises(learn.LearnError):
            self.lessons.remove("nothing like this")
        self.lessons.replace("alpha", "Alpha merged and moved elsewhere.", category="design")
        data, _ = self.lessons.parse()
        self.assertEqual(len(data["process"]), 1)
        self.assertIn("Alpha merged", data["design"][0])
        self.assertIn("consolidated", data["design"][0])
        self.lessons.remove("beta lesson")
        self.assertEqual(self.lessons.count(), 1)
        self.assertEqual(self.lessons.check()[0], [])

    def test_missing_block_is_an_error(self):
        with open(os.path.join(self.dir, "CLAUDE.md"), "w") as f:
            f.write("# no block here\n")
        problems, _ = self.lessons.check()
        self.assertTrue(problems)

    def test_retro_stamp_is_written_by_add_and_by_none(self):
        stamp = os.path.join(self.state, "last_retro")
        self.assertFalse(os.path.exists(stamp))
        self.lessons.none("nothing reusable")
        self.assertTrue(os.path.exists(stamp))
        os.remove(stamp)
        self.lessons.add("A new reusable lesson about stamps.", "process")
        self.assertTrue(os.path.exists(stamp))
        with open(os.path.join(self.state, "retro.log")) as f:
            self.assertIn("nothing new", f.read())

    def test_report_lists_near_duplicates(self):
        self.lessons.add("Always verify the restore with a whole file hash after cycles.", "verification")
        self.lessons.add("Verify the restore through whole file hash comparison after full cycles run.", "design")
        self.assertIn("near-duplicates", self.lessons.report())

    def test_a_relative_root_still_finds_the_workspace_state_dir(self):
        ws = os.path.join(self.dir, "ws")
        repo = os.path.join(ws, "repo")
        os.makedirs(os.path.join(ws, ".claude"))
        os.makedirs(repo)
        with open(os.path.join(repo, "CLAUDE.md"), "w") as f:
            f.write(TEMPLATE)
        cwd = os.getcwd()
        os.chdir(repo)
        try:
            learn.Lessons(".").add("A lesson written from a relative root path.", "process")
        finally:
            os.chdir(cwd)
        self.assertTrue(os.path.exists(os.path.join(ws, ".claude", ".state", "last_retro")))
        self.assertFalse(os.path.exists(os.path.join(repo, ".claude")))

    def test_lessons_live_at_workspace_scope_when_the_project_has_no_guide(self):
        ws = os.path.join(self.dir, "ws2")
        repo = os.path.join(ws, "repo")
        os.makedirs(repo)
        with open(os.path.join(ws, "CLAUDE.md"), "w") as f:
            f.write(TEMPLATE)
        lessons = learn.Lessons(repo, os.path.join(self.dir, "st"))
        self.assertEqual(lessons.claude, learn.Path(ws, "CLAUDE.md").resolve())
        lessons.add("A lesson that must land in the workspace guide.", "process")
        with open(os.path.join(ws, "CLAUDE.md")) as f:
            self.assertIn("workspace guide", f.read())
        self.assertFalse(os.path.exists(os.path.join(repo, "CLAUDE.md")))

    def test_a_project_level_guide_with_a_block_wins_over_the_workspace_one(self):
        ws = os.path.join(self.dir, "ws3")
        repo = os.path.join(ws, "repo")
        os.makedirs(repo)
        for folder in (ws, repo):
            with open(os.path.join(folder, "CLAUDE.md"), "w") as f:
                f.write(TEMPLATE)
        self.assertEqual(learn.Lessons(repo, self.state).claude, learn.Path(repo, "CLAUDE.md").resolve())

    @unittest.skipUnless(learn.Lessons(ROOT).claude.exists(), "no workspace CLAUDE.md (standalone clone)")
    def test_the_real_claude_md_is_valid_and_within_budget(self):
        problems, total = learn.Lessons(ROOT, self.state).check()
        self.assertEqual(problems, [])
        self.assertLessEqual(total, learn.HARD_LIMIT)


if __name__ == "__main__":
    unittest.main()
