"""10 Second Ninja X: the config-value strategy (one value in a save file inside the Proton prefix).

Synthetic fixtures only: a fake Steam home with two libraries (the game on the "SD card" one, the
Proton prefix with the save on the internal one), a tiny stand-in for data.win and an INI save with
the real layout. Nothing here needs game data.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from button_glyphs import engine  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402
from button_glyphs.profiles import ten_second_ninja_x  # noqa: E402
from button_glyphs.profiles.config_value import ConfigValueError, ConfigValueProfile  # noqa: E402

APPID = "435790"
BUILD = "1638978"
DATA_WIN = b"FORM" + bytes(range(256)) * 8
SAVE_REL = ten_second_ninja_x.PROFILE.file
GLYPH_DIR = os.path.join(ROOT, "py_modules", "button_glyphs", "glyphs")


def test_profile():
    """The real profile's layout, pinned to the tiny fixture build instead of the 140 MB one."""
    p = ten_second_ninja_x.PROFILE
    return ConfigValueProfile(
        appid=p.appid, name=p.name, save=p.file, section=p.section, key=p.key, values=p.values,
        value_pattern=p.value_pattern, roles=p.roles, revision=p.revision,
        original_hint=p.original_hint, notes=p.notes,
        builds={BUILD: {"file": "data.win", "size": len(DATA_WIN),
                        "sha256": hashlib.sha256(DATA_WIN).hexdigest()}})


def save_text(value="523.000000", filler="A", eol="\n", extra_before="", extra_in_story=""):
    lines = ["[my_save_data]", 'score="%s"' % filler * 1, 'name="Ninja é"'] + \
            ([extra_before] if extra_before else []) + \
            ["[storyfile]"] + ['level%d="%d.000000"' % (i, i) for i in range(1, 40)] + \
            ['buttons="%s"' % value] + ([extra_in_story] if extra_in_story else []) + \
            ['music="1.000000"']
    return eol.join(lines) + eol


def padded(text, size=32768):
    raw = text.encode("utf-8")
    return raw + b"\0" * (size - len(raw))


def value_at(data):
    i = data.index(b'buttons="') + len(b'buttons="')
    return data[i:i + 10].decode(), i


def without_value(data):
    """The file with the value bytes blanked: everything else must stay identical."""
    text, i = value_at(data)
    return data[:i] + b"?" * 10 + data[i + 10:]


def read(path):
    with open(path, "rb") as f:
        return f.read()


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.internal = os.path.join(self.home, ".local/share/Steam")
        self.sd = os.path.join(self.home, "sdcard")
        for lib in (self.internal, self.sd):
            os.makedirs(os.path.join(lib, "steamapps"))
        with open(os.path.join(self.internal, "steamapps", "libraryfolders.vdf"), "w") as f:
            f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n'
                    '\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % (self.internal, self.sd))
        self.install = os.path.join(self.sd, "steamapps", "common", "10 Second Ninja X")
        os.makedirs(self.install)
        write(os.path.join(self.install, "data.win"), DATA_WIN)
        self.manifest(flags="4", buildid=BUILD)
        self.save = os.path.join(self.internal, "steamapps", "compatdata", APPID, "pfx", SAVE_REL)
        write(self.save, padded(save_text()))
        self.proc = os.path.join(self.home, "proc")
        os.makedirs(self.proc)
        self.profile = test_profile()
        self.eng = engine.Engine(
            home=self.home, settings_dir=os.path.join(self.home, "settings"),
            runtime_dir=os.path.join(self.home, "runtime"), glyph_dir=GLYPH_DIR,
            profiles={APPID: self.profile}, proc_root=self.proc)
        self.backup = os.path.join(self.home, "runtime", "backups", APPID, "10SNX.sav.orig")

    def tearDown(self):
        shutil.rmtree(self.home)

    def manifest(self, flags="4", buildid=BUILD):
        with open(os.path.join(self.sd, "steamapps", "appmanifest_%s.acf" % APPID), "w") as f:
            f.write('"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"10 Second Ninja X"\n'
                    '\t"StateFlags"\t\t"%s"\n\t"installdir"\t\t"10 Second Ninja X"\n'
                    '\t"buildid"\t\t"%s"\n}\n' % (APPID, flags, buildid))

    def status(self):
        return next(g for g in self.eng.list_games() if g["appid"] == APPID)

    def cfg(self):
        return self.eng.load_config()["games"].get(APPID, {})

    def value(self):
        return value_at(read(self.save))[0]

    def run_game(self):
        os.makedirs(os.path.join(self.proc, "4242"), exist_ok=True)
        with open(os.path.join(self.proc, "4242", "cmdline"), "wb") as f:
            f.write(("/x/proton run %s/10SNX.exe" % self.install).encode().replace(b" ", b"\0"))

    def stop_game(self):
        shutil.rmtree(os.path.join(self.proc, "4242"), ignore_errors=True)


class ProfileTest(unittest.TestCase):
    def test_registered_and_describes_the_real_build(self):
        p = PROFILES[APPID]
        self.assertIs(p, ten_second_ninja_x.PROFILE)
        self.assertEqual((p.kind, p.revision, p.styles, p.textures), ("config-value", 1, {}, []))
        self.assertTrue(p.config_level)
        self.assertEqual(p.values, {"ps": "524.000000", "xbox": "523.000000"})
        self.assertEqual(p.patch_styles, ("ps", "xbox"))
        pin = p.builds["1638978"]
        self.assertEqual(pin["size"], 140512524)
        self.assertEqual(len(pin["sha256"]), 64)
        self.assertEqual([r for r, _ in p.roles], ["y", "x", "b", "a"])
        self.assertTrue(p.original_hint and p.notes)
        self.assertTrue(p.file.endswith("asyncsavegroup/10SNX.sav"))

    def test_values_must_match_pattern_and_length(self):
        p = ten_second_ninja_x.PROFILE
        kw = dict(appid="1", name="x", save="s", section="a", key="k", value_pattern=p.value_pattern,
                  builds={}, roles=[])
        with self.assertRaises(ValueError):
            ConfigValueProfile(values={"ps": "52.0"}, **kw)
        with self.assertRaises(ValueError):
            ConfigValueProfile(values={"ps": "524.000000", "xbox": "5230.000000"}, **kw)

    def test_locate_finds_the_value_inside_the_section(self):
        for eol in ("\n", "\r\n"):
            data = padded(save_text("525.000000", eol=eol))
            offset, value = ten_second_ninja_x.PROFILE.locate(data)
            self.assertEqual((value, data[offset:offset + 10]), ("525.000000", b"525.000000"))

    def test_locate_refuses_everything_else(self):
        p = ten_second_ninja_x.PROFILE
        good = save_text()
        bad = {
            "two lines": padded(save_text(extra_in_story='buttons="523.000000"')),
            "other section too": padded(good.replace("[my_save_data]", '[my_save_data]\nbuttons="1.000000"')),
            "wrong section only": padded(good.replace("[storyfile]", "[other]")
                                         .replace('music="1.000000"', "[storyfile]\nmusic=1")),
            "section twice": padded(good + "[storyfile]\nx=1\n"),
            "no section": padded(good.replace("[storyfile]", "[xx]")),
            "bad number": padded(good.replace('"523.000000"', '"5.0"')),
            "no quotes": padded(good.replace('"523.000000"', "523.000000")),
            "binary": bytes(range(256)) * 128,
            "data after text": padded(good) [:-4] + b"zzzz",
            "huge": (good.encode() * 40000),
            "empty": b"\0" * 32768,
        }
        for name, data in bad.items():
            with self.subTest(name), self.assertRaises(ConfigValueError):
                p.locate(data)

    def test_check_build(self):
        d = tempfile.mkdtemp()
        try:
            prof = test_profile()
            write(os.path.join(d, "data.win"), DATA_WIN)
            self.assertEqual(prof.check_build(BUILD, d), [])
            self.assertTrue(prof.check_build("1", d))
            write(os.path.join(d, "data.win"), DATA_WIN + b"x")
            self.assertIn("bytes", prof.check_build(BUILD, d, full=False)[0])
            write(os.path.join(d, "data.win"), b"X" + DATA_WIN[1:])
            self.assertEqual(prof.check_build(BUILD, d, full=False), [])        # cheap check: size only
            self.assertIn("checksum", prof.check_build(BUILD, d)[0])
            os.remove(os.path.join(d, "data.win"))
            self.assertTrue(prof.check_build(BUILD, d))
        finally:
            shutil.rmtree(d)


class ApplyTest(Base):
    def test_status_before_anything_is_original_and_ok(self):
        st = self.status()
        self.assertEqual((st["state"], st["style"], st["applied"]), ("ok", "original", "xbox"))
        self.assertIn("own setting", st["message"])
        self.assertEqual(st["roles"], [l for _, l in self.profile.roles])
        self.assertFalse(st["has_backup"])
        self.assertEqual(set(st), {"appid", "name", "supported", "buildid", "style", "applied", "state",
                                   "message", "last_event", "has_backup", "roles", "original_hint"})

    def test_full_cycle_changes_only_the_value_bytes(self):
        before = read(self.save)
        for style, want in (("ps", "524.000000"), ("xbox", "523.000000"), ("ps", "524.000000")):
            res = self.eng.set_style(APPID, style)
            self.assertTrue(res["ok"], res)
            data = read(self.save)
            self.assertEqual((value_at(data)[0], len(data)), (want, 32768))
            self.assertEqual(without_value(data), without_value(before))
            st = self.status()
            self.assertEqual((st["state"], st["applied"]), ("ok", style), st)
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(read(self.save), before)                      # untouched file: whole-file identical
        self.assertIsNone(self.cfg().get("original_value"))
        self.assertEqual(self.status()["state"], "ok")

    def test_game_rewrites_other_keys_between_steps(self):
        self.eng.set_style(APPID, "ps")
        # the game ran: new score, a longer name, a new key before [storyfile]; it kept our value
        game_text = save_text("524.000000", filler="BBBB", extra_before='newkey="1"')
        write(self.save, padded(game_text))
        self.assertEqual(self.eng.sync(APPID)["changed"], False)
        self.eng.set_style(APPID, "xbox")
        after = read(self.save)
        self.assertEqual(without_value(after), without_value(padded(game_text)))
        self.assertEqual(value_at(after)[0], "523.000000")
        # the game rewrote the file again while the style was xbox
        game2 = save_text("523.000000", filler="C", extra_before='another="22"')
        write(self.save, padded(game2))
        self.eng.set_style(APPID, "original")
        final = read(self.save)
        self.assertEqual(without_value(final), without_value(padded(game2)))
        self.assertEqual(value_at(final)[0], "523.000000")

    def test_original_restores_the_recorded_value_not_523(self):
        write(self.save, padded(save_text("525.000000")))
        before = read(self.save)
        self.eng.set_style(APPID, "ps")
        self.assertEqual(self.cfg()["original_value"], "525.000000")
        self.eng.set_style(APPID, "xbox")
        self.assertEqual(self.cfg()["original_value"], "525.000000")      # never stacked
        self.assertEqual(self.value(), "523.000000")
        self.eng.set_style(APPID, "original")
        self.assertEqual(read(self.save), before)

    def test_already_wanted_changes_nothing_and_records_nothing(self):
        os.utime(self.save, (1000, 1000))
        res = self.eng.set_style(APPID, "xbox")                          # file already holds 523
        self.assertTrue(res["ok"])
        self.assertFalse(res["changed"])
        self.assertEqual(os.stat(self.save).st_mtime, 1000)
        self.assertNotIn("original_value", self.cfg())
        self.assertFalse(os.path.exists(self.backup))
        self.assertEqual(self.status()["state"], "ok")
        # and back to Original: nothing recorded, nothing to do
        res = self.eng.set_style(APPID, "original")
        self.assertFalse(res["changed"])
        self.assertEqual(os.stat(self.save).st_mtime, 1000)

    def test_backup_of_the_whole_save_is_made_once(self):
        first = read(self.save)
        self.eng.set_style(APPID, "ps")
        self.assertEqual(read(self.backup), first)
        self.assertTrue(self.status()["has_backup"])
        self.eng.set_style(APPID, "original")
        write(self.save, padded(save_text("525.000000", filler="Z")))    # game progress, new content
        self.eng.set_style(APPID, "ps")
        self.assertEqual(read(self.backup), first)                        # not replaced
        self.assertFalse(os.path.exists(self.backup + ".tmp"))

    def test_game_in_one_library_prefix_in_another(self):
        self.assertTrue(self.install.startswith(self.sd))
        self.assertTrue(self.save.startswith(self.internal))
        self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
        self.assertEqual(self.value(), "524.000000")

    def test_ingame_option_is_put_back_by_the_periodic_sync(self):
        self.eng.set_style(APPID, "ps")
        data = bytearray(read(self.save))
        _, i = value_at(bytes(data))
        data[i:i + 10] = b"525.000000"
        write(self.save, bytes(data))                                     # player changed the option
        self.assertEqual(self.status()["state"], "pending")
        results = self.eng.sync_all("watch")
        self.assertEqual([r["changed"] for r in results], [True])
        self.assertEqual(self.value(), "524.000000")
        self.assertEqual(self.cfg()["original_value"], "523.000000")      # first record kept

    def test_update_to_a_known_build_keeps_working_and_hash_is_cached(self):
        calls = []
        real = self.profile._hash
        self.profile._hash = lambda path: (calls.append(path), real(path))[1]
        self.eng.set_style(APPID, "ps")
        self.eng.set_style(APPID, "xbox")
        self.eng.set_style(APPID, "ps")
        self.eng.sync_all("watch")
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.cfg()["verified_buildid"], BUILD)
        self.eng.list_games()                                             # status never hashes
        self.assertEqual(len(calls), 1)

    def test_sync_all_does_not_touch_games_on_original(self):
        os.utime(self.save, (1000, 1000))
        self.assertEqual(self.eng.sync_all("watch"), [])
        cfg = self.eng.load_config()
        cfg["games"][APPID] = {"style": "original"}
        self.eng.save_config(cfg)
        write(self.save, padded(save_text("525.000000")))
        before = read(self.save)
        self.assertEqual(self.eng.sync_all("watch"), [])
        self.assertEqual(read(self.save), before)

    def test_original_chosen_while_running_is_finished_by_the_watcher(self):
        before = read(self.save)
        self.eng.set_style(APPID, "ps")
        self.run_game()
        res = self.eng.set_style(APPID, "original")
        self.assertTrue(res["ok"])
        self.assertEqual(self.value(), "524.000000")                      # not touched while running
        self.assertEqual(self.status()["state"], "running")
        self.stop_game()
        self.assertEqual(self.status()["state"], "pending")
        self.assertEqual(self.status()["message"], "Original will be restored")
        self.assertEqual([r["changed"] for r in self.eng.sync_all("watch")], [True])
        self.assertEqual(read(self.save), before)
        self.assertEqual(self.eng.sync_all("watch"), [])

    def test_original_keeps_a_value_the_player_chose_in_game(self):
        self.eng.set_style(APPID, "ps")
        data = bytearray(read(self.save))
        _, i = value_at(bytes(data))
        data[i:i + 10] = b"525.000000"
        write(self.save, bytes(data))
        self.eng.set_style(APPID, "original")
        self.assertEqual(self.value(), "525.000000")
        self.assertNotIn("original_value", self.cfg())

    def test_crash_after_recording_before_writing_recovers(self):
        before = read(self.save)
        with mock.patch.object(engine.Engine, "_write_value", side_effect=OSError("power")):
            res = self.eng.set_style(APPID, "ps")
        self.assertFalse(res["ok"])
        self.assertEqual(read(self.save), before)
        with open(self.eng.config_path) as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["games"][APPID]["original_value"], "523.000000")
        self.assertEqual(self.status()["state"], "error")
        self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
        self.eng.set_style(APPID, "original")
        self.assertEqual(read(self.save), before)

    def test_running_and_updating_games_are_skipped(self):
        before = read(self.save)
        self.run_game()
        res = self.eng.set_style(APPID, "ps")
        self.assertTrue(res["ok"])
        self.assertFalse(res["changed"])
        self.assertEqual(read(self.save), before)
        self.assertEqual(self.status()["state"], "running")
        self.stop_game()
        self.manifest(flags="1026")
        self.assertEqual(self.status()["state"], "updating")
        self.assertFalse(self.eng.sync(APPID)["changed"])
        self.assertEqual(read(self.save), before)
        self.manifest(flags="4")
        self.assertEqual(self.status()["state"], "pending")
        self.assertEqual(self.status()["message"], "PlayStation will be applied")
        self.assertTrue(self.eng.sync_all("watch")[0]["changed"])
        self.assertEqual(self.status()["message"], "PlayStation glyphs active")


class RefusalTest(Base):
    def refused(self, fragment):
        before = read(self.save) if os.path.exists(self.save) else None
        res = self.eng.set_style(APPID, "ps")
        self.assertFalse(res["ok"], res)
        self.assertIn(fragment, res["errors"][0])
        if before is not None:
            self.assertEqual(read(self.save), before)
        self.assertFalse(os.path.exists(self.backup))
        self.assertNotIn("original_value", self.cfg())
        st = self.status()
        self.assertEqual(st["state"], "error")
        self.assertIn(fragment, st["message"])

    def test_no_save_says_to_start_the_game_once(self):
        os.remove(self.save)
        self.refused("start the game once, then close it")

    def test_save_in_two_libraries_is_ambiguous(self):
        other = os.path.join(self.sd, "steamapps", "compatdata", APPID, "pfx", SAVE_REL)
        write(other, read(self.save))
        self.refused("2 Steam libraries")

    def test_two_buttons_lines(self):
        write(self.save, padded(save_text(extra_in_story='buttons="523.000000"')))
        self.refused("expected 1")

    def test_key_in_the_wrong_section(self):
        text = save_text().replace('buttons="523.000000"\n', "")
        write(self.save, padded(text.replace("[my_save_data]", '[my_save_data]\nbuttons="523.000000"')))
        self.refused("not in [storyfile]")

    def test_binary_or_garbage_file(self):
        write(self.save, bytes(range(256)) * 128)
        self.refused("not in the expected layout")

    def test_unknown_build_id(self):
        self.manifest(buildid="999")
        self.refused("this game version is not supported yet")

    def test_wrong_data_win_size(self):
        write(os.path.join(self.install, "data.win"), DATA_WIN + b"extra")
        self.refused("this game version is not supported yet")

    def test_same_size_but_different_data_win(self):
        write(os.path.join(self.install, "data.win"), b"Y" + DATA_WIN[1:])
        self.refused("checksum")

    def test_missing_data_win(self):
        os.remove(os.path.join(self.install, "data.win"))
        self.refused("this game version is not supported yet")

    def test_refusal_does_not_block_original_restore(self):
        self.eng.set_style(APPID, "ps")
        self.manifest(buildid="999")                                      # game updated to an unknown build
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(self.value(), "523.000000")

    def test_error_outranks_pending_and_clears_after_fixing(self):
        os.remove(self.save)
        self.eng.set_style(APPID, "ps")
        self.assertEqual(self.status()["state"], "error")
        write(self.save, padded(save_text()))
        self.assertTrue(self.eng.sync_all("watch")[0]["ok"])
        self.assertEqual(self.status()["state"], "ok")

    def test_unconfigured_game_without_a_usable_save_is_neutral_not_an_error(self):
        # was: "error" for a game on Original; a never-configured game must not look broken
        os.remove(self.save)
        st = self.status()
        self.assertEqual((st["state"], st["message"]), ("ok", "Start the game once to enable this"))
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        write(self.save, bytes(range(256)) * 128)
        st = self.status()
        self.assertEqual(st["state"], "ok")
        self.assertIn("Not available yet", st["message"])
        write(self.save, padded(save_text(extra_in_story='buttons="523.000000"')))
        self.assertEqual(self.status()["state"], "ok")

    def test_configured_game_still_shows_the_error(self):
        os.remove(self.save)
        self.eng.set_style(APPID, "ps")
        self.assertEqual(self.status()["state"], "error")

    def test_oversized_save_is_refused(self):
        write(self.save, padded(save_text(), size=2 << 20))
        self.refused("not in the expected layout")


class RecordAndDurabilityTest(Base):
    """The way back (the record) is durable and independent of config.json; ours vs the player's."""

    def setUp(self):
        super().setUp()
        self.record = os.path.join(self.home, "runtime", "backups", APPID, "10SNX.record.json")

    def record_json(self):
        with open(self.record) as f:
            return json.load(f)

    def set_value(self, value):
        data = bytearray(read(self.save))
        _, i = value_at(bytes(data))
        data[i:i + 10] = value.encode()
        write(self.save, bytes(data))

    def test_save_config_is_flushed_before_and_after_the_rename(self):
        events = []
        real_fsync, real_replace = os.fsync, os.replace
        with mock.patch("os.fsync", side_effect=lambda fd: (events.append("fsync"), real_fsync(fd))[1]), \
                mock.patch("os.replace", side_effect=lambda a, b: (events.append("replace"), real_replace(a, b))[1]):
            self.eng.save_config({"version": 1, "games": {}})
        self.assertEqual(events, ["fsync", "replace", "fsync"])

    def test_backup_folder_is_flushed_after_the_rename(self):
        with mock.patch.object(engine, "fsync_dir", wraps=engine.fsync_dir) as fd:
            self.eng.set_style(APPID, "ps")
        self.assertIn(mock.call(os.path.dirname(self.backup)), fd.call_args_list)
        self.assertTrue(os.path.exists(self.backup))

    def test_record_is_on_disk_before_the_save_is_written(self):
        seen = []
        real = engine.Engine._write_value

        def spy(profile, path, offset, old, new):
            with open(self.eng.config_path) as f:
                seen.append((json.load(f)["games"][APPID], self.record_json(), os.path.exists(self.backup)))
            return real(profile, path, offset, old, new)
        with mock.patch.object(engine.Engine, "_write_value", side_effect=spy):
            self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
        cfg_entry, rec, had_backup = seen[0]
        self.assertEqual(cfg_entry["original_value"], "523.000000")
        self.assertEqual(cfg_entry["intended_value"], "524.000000")
        self.assertEqual((rec["original_value"], rec["intended_value"]), ("523.000000", "524.000000"))
        self.assertTrue(had_backup)
        self.assertEqual(self.record_json(), {"original_value": "523.000000", "intended_value": None,
                                             "written_value": "524.000000"})
        self.assertFalse(os.path.exists(self.record + ".tmp"))

    def test_losing_config_json_does_not_lose_the_way_back(self):
        write(self.save, padded(save_text("525.000000")))
        before = read(self.save)
        self.eng.set_style(APPID, "ps")
        os.remove(self.eng.config_path)
        self.assertEqual(self.status()["style"], "original")
        self.assertTrue(self.eng.set_style(APPID, "original")["ok"])
        self.assertEqual(read(self.save), before)
        self.assertFalse(os.path.exists(self.record))
        self.assertNotIn("original_value", self.cfg())

    def test_lost_config_then_new_style_keeps_the_first_original(self):
        self.eng.set_style(APPID, "ps")
        os.remove(self.eng.config_path)
        self.assertTrue(self.eng.set_style(APPID, "xbox")["ok"])
        self.assertEqual(self.cfg()["original_value"], "523.000000")
        self.assertEqual(self.record_json()["written_value"], "523.000000")

    def test_record_is_forgotten_when_original_is_done(self):
        self.eng.set_style(APPID, "ps")
        self.assertTrue(os.path.exists(self.record))
        self.eng.set_style(APPID, "original")
        self.assertFalse(os.path.exists(self.record))
        self.assertEqual(self.eng.sync_all("watch"), [])

    def test_invalid_record_does_not_wedge_the_game(self):
        self.eng.set_style(APPID, "ps")
        cfg = self.eng.load_config()
        cfg["games"][APPID].update(style="original", original_value="garbage")
        self.eng.save_config(cfg)
        before = read(self.save)
        res = self.eng.sync(APPID)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["errors"], [])
        self.assertFalse(res["changed"])
        self.assertEqual(read(self.save), before)                          # current value kept
        self.assertNotIn("original_value", self.cfg())
        self.assertIn("not valid", self.cfg()["last_event"])
        self.assertFalse(os.path.exists(self.record))
        self.assertEqual(self.eng.sync_all("watch"), [])                   # and it does not repeat
        self.assertEqual(self.status()["state"], "ok")

    def test_invalid_record_file_is_ignored_when_applying(self):
        os.makedirs(os.path.dirname(self.record))
        with open(self.record, "w") as f:
            f.write("{not json")
        self.assertTrue(self.eng.set_style(APPID, "ps")["ok"])
        self.assertEqual(self.record_json()["original_value"], "523.000000")

    def test_value_the_player_chose_in_game_is_not_overwritten_by_original(self):
        write(self.save, padded(save_text("525.000000")))
        self.eng.set_style(APPID, "xbox")           # writes 523 over the player's 525
        self.eng.set_style(APPID, "ps")             # writes 524
        self.set_value("523.000000")                # the player picks 523 in game: it is theirs now
        self.assertEqual(self.eng.set_style(APPID, "original")["changed"], False)
        self.assertEqual(self.value(), "523.000000")
        self.assertNotIn("original_value", self.cfg())
        self.assertFalse(os.path.exists(self.record))
        self.assertIn("kept", self.cfg()["last_event"])

    def test_ours_is_what_was_written_not_what_profile_values_says_today(self):
        self.eng.set_style(APPID, "ps")
        self.profile.values = {"ps": "526.000000", "xbox": "523.000000"}   # a future revision
        self.assertEqual(self.status()["state"], "pending")
        self.assertTrue(self.eng.set_style(APPID, "original")["changed"])
        self.assertEqual(self.value(), "523.000000")

    def test_crash_between_record_and_write_still_restores(self):
        self.eng.set_style(APPID, "ps")
        with mock.patch.object(engine.Engine, "_write_value", side_effect=OSError("power")):
            self.assertFalse(self.eng.set_style(APPID, "xbox")["ok"])
        self.assertEqual(self.value(), "524.000000")                        # untouched
        self.assertEqual(self.record_json()["intended_value"], "523.000000")
        self.eng.set_style(APPID, "original")
        self.assertEqual(self.value(), "523.000000")

    def test_crash_after_write_before_the_record_is_final_still_restores(self):
        write(self.save, padded(save_text("525.000000")))
        before = read(self.save)
        real = engine.Engine._store_record
        calls = []

        def flaky(self_, *a):
            calls.append(1)
            if len(calls) == 2:
                raise OSError("power")
            return real(self_, *a)
        with mock.patch.object(engine.Engine, "_store_record", flaky):
            self.assertFalse(self.eng.set_style(APPID, "ps")["ok"])
        self.assertEqual(self.value(), "524.000000")                        # the write did happen
        self.assertIsNone(self.record_json()["written_value"])
        self.assertEqual(self.record_json()["intended_value"], "524.000000")
        self.eng.set_style(APPID, "original")
        self.assertEqual(read(self.save), before)


class BuildCacheTest(Base):
    def count_hashes(self):
        calls = []
        real = self.profile._hash
        self.profile._hash = lambda path: (calls.append(path), real(path))[1]
        return calls

    def test_rehash_when_data_win_mtime_changes(self):
        calls = self.count_hashes()
        self.eng.set_style(APPID, "ps")
        self.eng.set_style(APPID, "xbox")
        self.assertEqual(len(calls), 1)
        data_win = os.path.join(self.install, "data.win")
        st = os.stat(data_win)
        os.utime(data_win, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        self.eng.set_style(APPID, "ps")
        self.assertEqual(len(calls), 2)
        self.eng.set_style(APPID, "xbox")
        self.assertEqual(len(calls), 2)

    def test_replaced_data_win_of_the_same_size_is_caught(self):
        self.eng.set_style(APPID, "ps")
        data_win = os.path.join(self.install, "data.win")
        write(data_win, b"Y" + DATA_WIN[1:])
        st = os.stat(data_win)
        os.utime(data_win, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
        before = read(self.save)
        res = self.eng.set_style(APPID, "xbox")
        self.assertFalse(res["ok"])
        self.assertIn("checksum", res["errors"][0])
        self.assertEqual(read(self.save), before)
        self.assertNotIn("verified_stamp", self.cfg())


if __name__ == "__main__":
    unittest.main()
