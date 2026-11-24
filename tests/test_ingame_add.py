"""tools/ingame_add.py: add / update / remove / list the In game registry, on a temp copy, never the real file.

Every case points the tool at a temp registry (--file, or by patching ingame.PATH when the case is about "the real
registry" behaviour such as regenerating SUPPORTED_GAMES.md). The Deck is a stub deck.sh, the way tests/test_deck_probe.py
does it: it logs its arguments and either answers with canned JSON or really runs the program the tool sent, against a
fake Steam library. The generator is stubbed. The shipped registry's SHA-256 is compared before and after the whole module.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))
sys.path.insert(0, str(ROOT / "tools"))

import ingame_add as ia  # noqa: E402
from button_glyphs import ingame  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402

REAL = Path(ingame.PATH)
REAL_SHA = hashlib.sha256(REAL.read_bytes()).hexdigest()
TODAY = ia.datetime.date.today().isoformat()

ENTRIES = [
    {"appid": "100", "name": "Alpha", "reported_by": "the project owner", "reported_on": "2026-11-20",
     "where": "Options > Controls", "note": "restart needed"},
    {"appid": "9000", "name": "Omega é", "reported_by": "someone", "reported_on": "2026-11-21",
     "where": "", "note": ""},
]


def setUpModule():
    assert hashlib.sha256(REAL.read_bytes()).hexdigest() == REAL_SHA


def tearDownModule():
    assert hashlib.sha256(REAL.read_bytes()).hexdigest() == REAL_SHA, "a test touched the shipped registry"


def dump(entries, indent=2):
    return json.dumps(entries, indent=indent, ensure_ascii=False) + "\n"


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.reg = os.path.join(self.dir, "ingame_games.json")
        self.write(dump(ENTRIES))
        # the stub deck.sh
        self.log, self.stdin, self.canned = (os.path.join(self.dir, n) for n in ("stub.log", "stub.stdin", "stub.json"))
        self.stub = os.path.join(self.dir, "deck.sh")
        with open(self.stub, "w") as f:
            f.write('#!/bin/bash\nprintf "%s\\n" "$*" >> "$STUB_LOG"\n'
                    'case "$1" in\n  --check) exit "${STUB_CHECK_RC:-0}";;\n  true) exit 0;;\n'
                    '  "python3 -") cat > "$STUB_STDIN"\n'
                    '    if [ -n "${STUB_RUN:-}" ]; then "$STUB_RUN" "$STUB_STDIN"; else cat "$STUB_JSON"; fi;;\n'
                    '  *) exit 64;;\nesac\n')
        os.chmod(self.stub, 0o755)
        self.env = {"DECK_SH": self.stub, "STUB_LOG": self.log, "STUB_STDIN": self.stdin, "STUB_JSON": self.canned,
                    "STUB_CHECK_RC": "0"}
        os.environ.pop(ia.ENV_FILE, None)
        self.regen = mock.patch.object(ia, "regenerate", return_value=0)
        self.regen_mock = self.regen.start()
        self.addCleanup(self.regen.stop)

    # ---- helpers
    def write(self, text):
        with open(self.reg, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def read(self):
        with open(self.reg, encoding="utf-8", newline="") as f:
            return f.read()

    def raw(self):
        return json.loads(self.read())

    def run_main(self, *argv, env=None, file=True):
        args = list(argv) + (["--file", self.reg] if file else [])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, dict(self.env, **(env or {}))), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            try:
                code = ia.main(args)
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def canned_name(self, name="Deck Game"):
        with open(self.canned, "w") as f:
            f.write("a banner\n" + json.dumps({"ok": True, "appid": "x", "name": name}) + "\n")

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return f.read().splitlines()

    def leftovers(self):
        return [n for n in os.listdir(self.dir) if n.endswith(".tmp") or n.startswith(".ingame")]


class AddTests(Base):
    def test_add_with_a_name_writes_a_sorted_entry_with_today_and_the_default_reporter(self):
        code, out, err = self.run_main("add", "500", "--name", "Mid  Game", "--where", "Options > Interface")
        self.assertEqual(code, 0, (out, err))
        self.assertEqual([e["appid"] for e in self.raw()], ["100", "500", "9000"])      # numeric order, not text order
        self.assertEqual(self.raw()[1], {"appid": "500", "name": "Mid Game", "reported_by": "the project owner",
                                         "reported_on": TODAY, "where": "Options > Interface", "note": ""})
        self.assertEqual(self.calls(), [])                                              # --name: the Deck is not asked
        self.assertEqual(len(ingame.load(self.reg)), 3)

    def test_the_flags_are_all_honoured(self):
        code, _, _ = self.run_main("add", "7", "--name", "N", "--reported-by", "Ana", "--on", "2026-01-02",
                                   "--note", "tested by hand", "--where", "Settings")
        self.assertEqual(code, 0)
        e = self.raw()[0]
        self.assertEqual((e["appid"], e["reported_by"], e["reported_on"], e["note"], e["where"]),
                         ("7", "Ana", "2026-01-02", "tested by hand", "Settings"))

    def test_the_next_steps_are_three_lines(self):
        _, out, _ = self.run_main("add", "7", "--name", "N")
        lines = out.splitlines()
        self.assertEqual(len(lines), 3)
        self.assertIn("Changed: added 7", lines[0])
        self.assertIn("run the tests", lines[1])
        self.assertIn("the plugin never changes this game; record how you verified it in --note", lines[2])

    def test_the_name_comes_from_the_decks_manifest(self):
        self.canned_name("  Deck   Game ")
        code, out, err = self.run_main("add", "321")
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(self.raw()[1]["name"], "Deck Game")
        self.assertEqual(self.calls(), ["--check", "true", "python3 -"])                # the command text is only this
        with open(self.stdin) as f:
            script = f.read()
        self.assertIn("def remote_main(", script)
        self.assertTrue(script.rstrip().endswith("remote_main(%r, '321')" % ia.STEAM_PY.read_text(encoding="utf-8")))
        self.assertNotIn("steamapps/common", script.replace(ia.STEAM_PY.read_text(encoding="utf-8"), ""))

    def test_the_program_sent_to_the_deck_really_reads_the_name_from_a_manifest(self):
        home = os.path.join(self.dir, "home")
        steamapps = os.path.join(home, ".local", "share", "Steam", "steamapps")
        os.makedirs(steamapps)
        with open(os.path.join(steamapps, "libraryfolders.vdf"), "w") as f:
            f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % os.path.dirname(steamapps))
        with open(os.path.join(steamapps, "appmanifest_555.acf"), "w") as f:
            f.write('"AppState"\n{\n\t"appid"\t\t"555"\n\t"name"\t\t"Real Manifest Game"\n\t"installdir"\t\t"x"\n}\n')
        runner = os.path.join(self.dir, "run.sh")
        with open(runner, "w") as f:
            f.write('#!/bin/bash\nexec "%s" "$1"\n' % sys.executable)
        os.chmod(runner, 0o755)
        code, out, err = self.run_main("add", "555", env={"STUB_RUN": runner, "INGAME_ADD_HOME": home,
                                                          "HOME": os.path.join(self.dir, "nohome")})
        self.assertEqual(code, 0, (out, err))
        self.assertEqual(self.raw()[1]["name"], "Real Manifest Game")
        # an app that has no manifest: the program answers ok=false and the tool asks for --name
        code, _, err = self.run_main("add", "556", env={"STUB_RUN": runner, "INGAME_ADD_HOME": home})
        self.assertEqual(code, 2)
        self.assertIn("--name", err)

    def test_an_unreachable_deck_without_a_name_is_exit_two_and_nothing_is_written(self):
        before = self.read()
        code, _, err = self.run_main("add", "321", env={"STUB_CHECK_RC": "1"})
        self.assertEqual(code, 2)
        self.assertIn("Deck is unreachable", err)
        self.assertIn("--name", err)
        self.assertEqual(self.calls(), ["--check"])
        self.assertEqual(self.read(), before)
        self.regen_mock.assert_not_called()

    def test_an_unreachable_deck_with_a_name_is_fine_and_is_not_asked(self):
        code, _, _ = self.run_main("add", "321", "--name", "Typed", env={"STUB_CHECK_RC": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(self.calls(), [])

    def test_an_app_not_installed_or_garbage_from_the_deck_asks_for_a_name_and_never_guesses(self):
        before = self.read()
        with open(self.canned, "w") as f:
            f.write(json.dumps({"ok": False, "appid": "321", "name": "", "error": "the app is not installed"}) + "\n")
        code, _, err = self.run_main("add", "321")
        self.assertEqual((code, "--name" in err), (2, True))
        with open(self.canned, "w") as f:
            f.write("Traceback (most recent call last): boom\n")
        code, _, err = self.run_main("add", "321")
        self.assertEqual((code, "--name" in err), (2, True))
        with open(self.canned, "w") as f:
            f.write(json.dumps({"ok": True, "appid": "321", "name": "   "}) + "\n")
        self.assertEqual(self.run_main("add", "321")[0], 2)
        self.assertEqual(self.read(), before)

    def test_a_missing_deck_sh_is_exit_two_not_a_traceback(self):
        code, _, err = self.run_main("add", "321", env={"DECK_SH": os.path.join(self.dir, "nope.sh")})
        self.assertEqual(code, 2)
        self.assertIn("--name", err)


class RefusalTests(Base):
    def assertUntouched(self, before):
        self.assertEqual(self.read(), before)
        self.assertEqual(self.leftovers(), [])
        self.regen_mock.assert_not_called()

    def test_a_duplicate_is_refused_with_the_existing_entry_and_the_update_hint(self):
        before = self.read()
        code, out, err = self.run_main("add", "100", "--name", "Other")
        self.assertEqual(code, 1)
        self.assertIn("already in the list", err)
        self.assertIn("update 100", err)
        self.assertEqual(json.loads(out)["name"], "Alpha")                              # the existing entry is printed
        self.assertUntouched(before)
        self.assertEqual(self.calls(), [])                                              # decided before the Deck is asked

    def test_a_game_with_a_plugin_profile_is_refused(self):
        before = self.read()
        appid = next(iter(PROFILES))
        code, _, err = self.run_main("add", appid, "--name", "Whatever")
        self.assertEqual(code, 1)
        self.assertIn("already supported by the plugin", err)
        self.assertIn("cannot also be In game", err)
        self.assertUntouched(before)
        self.assertEqual(self.calls(), [])

    def test_every_registered_profile_is_refused(self):
        before = self.read()
        for appid in PROFILES:
            with self.subTest(appid=appid):
                self.assertEqual(self.run_main("add", appid, "--name", "x")[0], 1)
        self.assertUntouched(before)

    def test_a_bad_appid_is_refused(self):
        before = self.read()
        for bad in ("12a", "", "-5", "1 2", "١٢"):
            with self.subTest(appid=bad):
                code, _, err = self.run_main("add", bad, "--name", "x")
                self.assertEqual(code, 1, err)
                self.assertIn("appid", err)
        self.assertUntouched(before)
        self.assertEqual(self.calls(), [])

    def test_an_empty_name_is_refused_and_the_file_is_byte_identical(self):
        before = self.read()
        for name in ("", "   "):
            code, _, err = self.run_main("add", "42", "--name", name)
            self.assertEqual(code, 1)
            self.assertIn("nothing written", err)
            self.assertIn("name", err)
        self.assertUntouched(before)

    def test_an_empty_reporter_is_a_usage_error(self):
        before = self.read()
        self.assertEqual(self.run_main("add", "42", "--name", "x", "--reported-by", " ")[0], 64)
        self.assertUntouched(before)

    def test_usage_errors_are_exit_64(self):
        self.assertEqual(self.run_main()[0], 64)
        self.assertEqual(self.run_main("add")[0], 64)
        self.assertEqual(self.run_main("add", "1", "--name", "x", "--on", "tomorrow")[0], 64)
        self.assertEqual(self.run_main("update", "100")[0], 64)                         # nothing to change
        self.assertEqual(self.run_main("frobnicate", "1")[0], 64)

    def test_a_broken_registry_is_refused_not_repaired(self):
        for bad in ("{not json", "[1]", json.dumps([ENTRIES[0], ENTRIES[0]]), ""):
            with self.subTest(bad=bad[:12]):
                self.write(bad)
                code, _, err = self.run_main("add", "42", "--name", "x")
                self.assertEqual(code, 1)
                self.assertIn("not a valid In game list", err)
                self.assertEqual(self.read(), bad)
        os.unlink(self.reg)
        self.assertEqual(self.run_main("add", "42", "--name", "x")[0], 1)               # missing file: refused, not created
        self.assertFalse(os.path.exists(self.reg))


class UpdateRemoveListTests(Base):
    def test_update_changes_where_and_note_only(self):
        code, out, err = self.run_main("update", "9000", "--where", "Settings > Gamepad", "--note", "Auto shows Xbox")
        self.assertEqual(code, 0, (out, err))
        e = self.raw()[1]
        self.assertEqual((e["where"], e["note"]), ("Settings > Gamepad", "Auto shows Xbox"))
        self.assertEqual((e["name"], e["reported_by"], e["reported_on"]), ("Omega é", "someone", "2026-11-21"))
        self.assertEqual(self.raw()[0], ENTRIES[0])
        self.assertIn("Changed: updated 9000", out)

    def test_update_can_clear_a_field_and_an_unchanged_update_writes_nothing(self):
        self.assertEqual(self.run_main("update", "100", "--note", "")[0], 0)
        self.assertEqual(self.raw()[0]["note"], "")
        before, mtime = self.read(), os.stat(self.reg).st_mtime_ns
        code, out, _ = self.run_main("update", "100", "--note", "")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to change", out)
        self.assertEqual((self.read(), os.stat(self.reg).st_mtime_ns), (before, mtime))

    def test_update_and_remove_of_an_unknown_appid_are_refused(self):
        before = self.read()
        for argv in (("update", "5", "--where", "x"), ("remove", "5")):
            code, _, err = self.run_main(*argv)
            self.assertEqual(code, 1)
            self.assertIn("not in the list", err)
        self.assertEqual(self.read(), before)

    def test_remove_and_removing_the_last_entry(self):
        self.assertEqual(self.run_main("remove", "100")[0], 0)
        self.assertEqual([e["appid"] for e in self.raw()], ["9000"])
        self.assertEqual(self.run_main("remove", "9000")[0], 0)
        self.assertEqual(self.read(), "[]\n")
        self.assertEqual(ingame.load(self.reg), [])
        self.assertEqual(self.run_main("add", "1", "--name", "Back")[0], 0)             # and it can grow again
        self.assertEqual([e["appid"] for e in self.raw()], ["1"])

    def test_list_prints_every_game_and_writes_nothing(self):
        before, mtime = self.read(), os.stat(self.reg).st_mtime_ns
        code, out, _ = self.run_main("list")
        self.assertEqual(code, 0)
        self.assertIn("100", out)
        self.assertIn("Alpha", out)
        self.assertIn("Options > Controls", out)
        self.assertIn("note: restart needed", out)
        self.assertIn("Omega é", out)
        self.assertEqual((self.read(), os.stat(self.reg).st_mtime_ns), (before, mtime))
        self.write("[]\n")
        self.assertIn("No In game games yet", self.run_main("list")[1])

    def test_the_env_variable_points_at_another_file(self):
        code, out, _ = self.run_main("list", file=False, env={ia.ENV_FILE: self.reg})
        self.assertEqual(code, 0)
        self.assertIn("Alpha", out)


class FormatAndAtomicTests(Base):
    def test_resaving_an_unchanged_file_gives_identical_bytes(self):
        for indent, text in ((2, dump(ENTRIES)), (4, dump(ENTRIES, 4)), ("\t", dump(ENTRIES, "\t")), (2, "[]\n")):
            with self.subTest(indent=indent):
                raw = json.loads(text)
                self.assertEqual(ia.render(raw, ia.detect_indent(text)), text)
        text = REAL.read_text(encoding="utf-8")                                         # and the shipped file itself
        self.assertEqual(ia.render(json.loads(text), ia.detect_indent(text)), text)

    def test_an_add_then_a_remove_gives_the_original_bytes_back(self):
        before = self.read()
        self.assertEqual(self.run_main("add", "500", "--name", "Mid")[0], 0)
        self.assertNotEqual(self.read(), before)
        self.assertEqual(self.run_main("remove", "500")[0], 0)
        self.assertEqual(self.read(), before)

    def test_indentation_key_order_utf8_and_trailing_newline_are_kept(self):
        reordered = [{"name": "Zed", "appid": "10", "reported_on": "2026-11-20", "reported_by": "x", "where": "W", "note": ""}]
        self.write(dump(reordered, 4))
        self.assertEqual(self.run_main("add", "20", "--name", "Café デ", "--where", "x")[0], 0)
        text = self.read()
        self.assertTrue(text.endswith("}\n]\n") and not text.endswith("\n\n"))
        self.assertIn('\n    {\n        "name": "Zed"', text)                           # 4-space indent, first entry untouched
        self.assertIn('        "name": "Café デ",\n        "appid": "20"', text)  # new entry follows the neighbours' key order
        self.assertNotIn("\\u", text)                                                   # UTF-8 as is, no escapes
        text.encode("utf-8")
        self.assertEqual(self.raw()[1]["name"], "Café デ")

    def test_a_file_that_omits_empty_optional_fields_keeps_omitting_them(self):
        self.write(dump([{"appid": "10", "name": "Z", "reported_by": "x", "reported_on": "2026-11-20"}]))
        self.assertEqual(self.run_main("add", "20", "--name", "Y")[0], 0)
        self.assertEqual(set(self.raw()[1]), {"appid", "name", "reported_by", "reported_on"})
        self.assertEqual(self.run_main("add", "30", "--name", "X", "--note", "n")[0], 0)
        self.assertEqual(self.raw()[2]["note"], "n")

    def test_the_file_mode_survives_the_replace(self):
        os.chmod(self.reg, 0o640)
        self.assertEqual(self.run_main("add", "5", "--name", "x")[0], 0)
        self.assertEqual(stat.S_IMODE(os.stat(self.reg).st_mode), 0o640)

    def test_a_good_write_leaves_no_temp_file(self):
        self.assertEqual(self.run_main("add", "5", "--name", "x")[0], 0)
        self.assertEqual(self.leftovers(), [])
        self.assertEqual(sorted(os.listdir(self.dir)), sorted(["ingame_games.json", "deck.sh"]))

    def test_the_write_goes_through_a_temp_file_in_the_same_folder_fsync_then_replace(self):
        events = []
        real_replace, real_fsync = os.replace, os.fsync
        with mock.patch.object(os, "replace", side_effect=lambda a, b: (events.append(("replace", a, b)), real_replace(a, b))[1]), \
                mock.patch.object(os, "fsync", side_effect=lambda fd: (events.append(("fsync",)), real_fsync(fd))[1]):
            self.assertEqual(self.run_main("add", "5", "--name", "x")[0], 0)
        kinds = [e[0] for e in events]
        self.assertEqual(kinds[:2], ["fsync", "replace"])                               # the data is durable before the swap
        _, tmp, dest = events[1]
        self.assertEqual((os.path.dirname(tmp), dest), (self.dir, self.reg))

    def test_a_failure_before_the_swap_leaves_the_file_identical_and_no_temp(self):
        before = self.read()
        with mock.patch.object(os, "replace", side_effect=OSError("disk full")):
            code, _, err = self.run_main("add", "5", "--name", "x")
        self.assertEqual(code, 1)
        self.assertIn("unchanged", err)
        self.assertEqual(self.read(), before)
        self.assertEqual(self.leftovers(), [])
        with mock.patch.object(os, "fsync", side_effect=OSError("io error")):
            self.assertEqual(self.run_main("add", "5", "--name", "x")[0], 1)
        self.assertEqual(self.read(), before)
        self.assertEqual(self.leftovers(), [])
        self.regen_mock.assert_not_called()

    def test_the_validator_runs_before_anything_is_written(self):
        before = self.read()
        real = ingame.validate

        def strict(entries):
            if len(entries) == len(ENTRIES) + 1:             # only the RESULT of the add (the old file and the appid check pass)
                raise ingame.InGameError("the new list is bad")
            return real(entries)
        with mock.patch.object(ia, "atomic_write") as write, mock.patch.object(ia.ingame, "validate", side_effect=strict):
            code, _, err = self.run_main("add", "5", "--name", "x")
        self.assertEqual(code, 1)
        self.assertIn("the new list is bad", err)
        write.assert_not_called()
        self.assertEqual(self.read(), before)

    def test_every_write_is_validated_with_the_plugins_own_validator(self):
        seen = []
        real = ingame.validate

        def spy(entries):
            seen.append(len(entries))
            return real(entries)
        with mock.patch.object(ia.ingame, "validate", side_effect=spy):
            self.assertEqual(self.run_main("add", "5", "--name", "x")[0], 0)
            self.assertEqual(self.run_main("update", "5", "--note", "n")[0], 0)
            self.assertEqual(self.run_main("remove", "5")[0], 0)
        self.assertIn(3, seen)                                                          # the 3-entry result of the add
        self.assertGreaterEqual(len(seen), 9)


class DryRunAndRegenTests(Base):
    def real_registry(self):
        """Make `self.reg` look like the shipped registry for the regeneration rule."""
        patcher = mock.patch.object(ia.ingame, "PATH", self.reg)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_dry_run_prints_the_entry_and_the_diff_and_writes_nothing(self):
        before, mtime = self.read(), os.stat(self.reg).st_mtime_ns
        code, out, _ = self.run_main("add", "500", "--name", "Mid", "--where", "Options", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn('"name": "Mid"', out)
        self.assertIn('+    "appid": "500",', out)
        self.assertIn("--- before", out)
        self.assertEqual((self.read(), os.stat(self.reg).st_mtime_ns), (before, mtime))
        self.assertEqual(self.leftovers(), [])
        self.regen_mock.assert_not_called()
        for argv in (("update", "100", "--note", "n"), ("remove", "100")):
            self.assertEqual(self.run_main(*argv, "--dry-run")[0], 0)
            self.assertEqual(self.read(), before)

    def test_dry_run_still_applies_the_refusals(self):
        self.assertEqual(self.run_main("add", "100", "--name", "x", "--dry-run")[0], 1)
        self.assertEqual(self.run_main("add", "5", "--name", "", "--dry-run")[0], 1)

    def test_the_generator_runs_after_a_real_write_on_the_real_registry(self):
        self.real_registry()
        order = []
        self.regen_mock.side_effect = lambda: (order.append(("regen", "500" in self.read())), 0)[1]
        code, out, _ = self.run_main("add", "500", "--name", "Mid", file=False)
        self.assertEqual(code, 0)
        self.regen_mock.assert_called_once_with()
        self.assertEqual(order, [("regen", True)])                                      # the list is on disk when it runs
        self.assertIn("SUPPORTED_GAMES.md regenerated", out)

    def test_update_and_remove_regenerate_too(self):
        self.real_registry()
        self.run_main("update", "100", "--note", "n", file=False)
        self.run_main("remove", "100", file=False)
        self.assertEqual(self.regen_mock.call_count, 2)

    def test_no_regen_skips_the_generator(self):
        self.real_registry()
        code, out, _ = self.run_main("add", "500", "--name", "Mid", "--no-regen", file=False)
        self.assertEqual(code, 0)
        self.regen_mock.assert_not_called()
        self.assertIn("(--no-regen)", out)

    def test_another_file_never_regenerates_the_real_page(self):
        code, out, _ = self.run_main("add", "500", "--name", "Mid")                    # --file is a temp file
        self.assertEqual(code, 0)
        self.regen_mock.assert_not_called()
        self.assertIn("another --file", out)

    def test_a_failing_generator_is_reported_after_the_write(self):
        self.real_registry()
        self.regen_mock.return_value = 2
        code, _, err = self.run_main("add", "500", "--name", "Mid", file=False)
        self.assertEqual(code, 1)
        self.assertIn("was written", err)
        self.assertIn("gen_supported_games.py", err)
        self.assertIn("500", self.read())

    def test_the_real_generator_is_the_one_in_tools(self):
        import gen_supported_games
        self.regen.stop()
        self.addCleanup(self.regen.start)
        with mock.patch.object(gen_supported_games, "main", return_value=0) as main:
            self.assertEqual(ia.regenerate(), 0)
        main.assert_called_once_with([])


class RealRegistryTests(unittest.TestCase):
    def test_the_shipped_registry_is_untouched_and_survives_a_round_trip_in_memory(self):
        self.assertEqual(hashlib.sha256(REAL.read_bytes()).hexdigest(), REAL_SHA)
        self.assertIn("1984270", ingame.by_appid())

    def test_the_cli_as_a_program_lists_the_shipped_registry(self):
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "ingame_add.py"), "list"], capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Digimon Story Time Stranger", done.stdout)
        self.assertEqual(subprocess.run([sys.executable, str(ROOT / "tools" / "ingame_add.py")], capture_output=True,
                                        text=True, timeout=60).returncode, 64)


if __name__ == "__main__":
    unittest.main()
