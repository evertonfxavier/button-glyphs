"""steam.is_running: the guard that keeps every strategy from writing into a running game.

It may only ever see MORE than a plain substring test of the install path: mount aliases of the SD
card (/run/media/<user>/<label> and /run/media/<label>), the realpath, and the launcher's
`AppId=<appid>` token.
"""
import os
import shutil
import sys
import tempfile
import unicodedata
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))

from button_glyphs import steam  # noqa: E402

APPID = "389170"
INSTALLDIR = "A Lenda do Herói - O Jogo"


def posix_cmd(folder):
    return ["proton waitforexitandrun %s/DumaLegend.exe" % folder]


def windows_cmd(folder):
    return ["Z:" + (folder + "/DumaLegend.exe").replace("/", "\\")]


class GuardCase(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())      # macOS: /var is a link to /private/var
        self.media = os.path.join(self.root, "run", "media")
        self.real_mount = os.path.join(self.media, "deck", "LX512")          # as libraryfolders.vdf has it
        self.alias_mount = os.path.join(self.media, "LX512")                 # the other name of the same card
        self.common = os.path.join(self.real_mount, "steamapps", "common")
        os.makedirs(os.path.join(self.common, INSTALLDIR))
        os.symlink(self.real_mount, self.alias_mount)
        patcher = mock.patch.object(steam, "MEDIA_ROOT", self.media)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = {"appid": APPID, "install_path": os.path.join(self.common, INSTALLDIR)}

    def tearDown(self):
        shutil.rmtree(self.root)

    def via_alias(self, name=INSTALLDIR):
        return os.path.join(self.alias_mount, "steamapps", "common", name)


class MountAliasTest(GuardCase):
    def test_the_exact_reproduction_posix_and_windows(self):
        """Library path .../deck/LX512, process started through .../LX512 (the same folder)."""
        folder = self.via_alias()
        self.assertEqual(os.path.realpath(folder), os.path.realpath(self.app["install_path"]))
        self.assertTrue(steam.is_running(self.app, posix_cmd(folder)))
        self.assertTrue(steam.is_running(self.app, windows_cmd(folder)))

    def test_without_the_alias_rule_it_was_a_miss(self):
        """The same cmdlines are NOT seen when the folder does not live under the media root."""
        with mock.patch.object(steam, "MEDIA_ROOT", os.path.join(self.root, "elsewhere")):
            self.assertFalse(steam.is_running(self.app, posix_cmd(self.via_alias())))
            self.assertFalse(steam.is_running(self.app, windows_cmd(self.via_alias())))

    def test_the_library_path_may_be_the_alias_and_the_process_the_real_one(self):
        app = dict(self.app, install_path=self.via_alias())
        real = os.path.join(self.common, INSTALLDIR)
        self.assertTrue(steam.is_running(app, posix_cmd(real)))              # realpath
        self.assertTrue(steam.is_running(app, windows_cmd(real)))

    def test_the_user_form_is_derived_from_the_label_form(self):
        os.remove(self.alias_mount)                                          # now <label> is the real folder...
        os.rename(self.real_mount, self.alias_mount)
        os.symlink(self.alias_mount, self.real_mount)                        # ...and <user>/<label> the alias
        app = dict(self.app, install_path=os.path.join(self.alias_mount, "steamapps", "common", INSTALLDIR))
        through_user = os.path.join(self.real_mount, "steamapps", "common", INSTALLDIR)
        self.assertTrue(steam.is_running(app, posix_cmd(through_user)))
        self.assertTrue(steam.is_running(app, windows_cmd(through_user)))

    def test_another_volume_with_the_same_label_is_not_an_alias(self):
        other = os.path.join(self.media, "other", "LX512", "steamapps", "common", INSTALLDIR)
        os.makedirs(other)                                                   # a different card, same folder name
        self.assertFalse(steam.is_running(self.app, posix_cmd(other)))
        self.assertFalse(steam.is_running(self.app, windows_cmd(other)))

    def test_a_folder_that_exists_under_another_user_but_is_not_the_same_card_is_not_an_alias(self):
        app = dict(self.app, install_path=self.via_alias())                  # <label> form: users are looked up
        other = os.path.join(self.media, "other", "LX512", "steamapps", "common", INSTALLDIR)
        os.makedirs(other)                                                   # exists, but another card
        self.assertFalse(steam.is_running(app, posix_cmd(other)))
        self.assertFalse(steam.is_running(app, windows_cmd(other)))
        self.assertTrue(steam.is_running(app, posix_cmd(self.app["install_path"])))   # the real alias still works

    def test_a_different_game_in_the_same_library_is_not_matched(self):
        for name in ("Other Game", INSTALLDIR + " 2", INSTALLDIR[:-3]):
            self.assertFalse(steam.is_running(self.app, posix_cmd(self.via_alias(name))), name)
            self.assertFalse(steam.is_running(self.app, windows_cmd(self.via_alias(name))), name)
            self.assertFalse(steam.is_running(self.app, posix_cmd(os.path.join(self.common, name))), name)

    def test_only_paths_under_the_media_root_are_aliased(self):
        outside = os.path.join(self.root, "lib", "deck", "LX512", "steamapps", "common", INSTALLDIR)
        os.makedirs(outside)
        os.symlink(os.path.join(self.root, "lib", "deck", "LX512"), os.path.join(self.root, "lib", "LX512"))
        app = dict(self.app, install_path=outside)
        elsewhere = os.path.join(self.root, "lib", "LX512", "steamapps", "common", INSTALLDIR)
        self.assertFalse(steam.is_running(app, posix_cmd(elsewhere)))        # not a media path, not derived
        self.assertEqual(steam.install_forms(outside)[0], outside + "/")
        # realpath still catches the case where Steam's own path is the symlink
        self.assertTrue(steam.is_running(dict(self.app, install_path=elsewhere), posix_cmd(outside)))

    def test_install_forms_keep_the_reported_path_first_and_only_add(self):
        forms = steam.install_forms(self.app["install_path"] + "/")
        self.assertEqual(forms[0], self.app["install_path"] + "/")
        self.assertIn(self.via_alias() + "/", forms)
        self.assertEqual(len(forms), len(set(forms)))

    def test_only_the_media_root_is_ever_listed(self):
        """No folder of a mounted volume is listed, so an unreachable mount cannot hang the guard."""
        listed = []
        real = os.listdir
        app = dict(self.app, install_path=self.via_alias())                  # the <label> form lists the users
        with mock.patch.object(steam.os, "listdir", lambda p: listed.append(p) or real(p)):
            self.assertTrue(steam.is_running(app, posix_cmd(self.app["install_path"])))
            self.assertTrue(steam.is_running(self.app, windows_cmd(self.via_alias())))
        self.assertEqual(set(listed), {self.media + "/"})

    def test_a_failing_filesystem_call_never_fails_the_guard(self):
        for error in (OSError("unreachable"), ValueError("embedded null byte")):
            with mock.patch.object(steam.os.path, "realpath", side_effect=error):
                self.assertEqual(steam.install_forms(self.app["install_path"]), [self.app["install_path"] + "/"])
                self.assertTrue(steam.is_running(self.app, posix_cmd(self.app["install_path"])))
                self.assertFalse(steam.is_running(self.app, ["/usr/bin/steam -gamepadui"]))
            with mock.patch.object(steam.os.path, "samefile", side_effect=error):
                self.assertTrue(steam.is_running(self.app, posix_cmd(self.app["install_path"])))
        with mock.patch.object(steam.os, "listdir", side_effect=OSError("no")):
            app = dict(self.app, install_path=self.via_alias())
            self.assertTrue(steam.is_running(app, posix_cmd(self.via_alias())))

    def test_a_missing_media_folder_does_not_break_anything(self):
        with mock.patch.object(steam, "MEDIA_ROOT", os.path.join(self.root, "nope")):
            self.assertEqual(steam.install_forms("/nope/steamapps/common/x"), ["/nope/steamapps/common/x/"])
            self.assertEqual(steam.install_forms(""), ["/"])             # as before for an empty path


class AppIdTokenTest(GuardCase):
    def other_place(self):
        return {"appid": APPID, "install_path": "/somewhere/else/steamapps/common/Nothing"}

    def test_the_launcher_token_alone_is_enough(self):
        app = self.other_place()
        self.assertTrue(steam.is_running(app, ["reaper SteamLaunch AppId=%s -- /usr/bin/proton run x.exe" % APPID]))
        self.assertTrue(steam.is_running(app, ["reaper SteamLaunch AppId=%s" % APPID]))
        self.assertTrue(steam.is_running(app, ["AppId=%s" % APPID]))

    def test_a_longer_number_or_a_longer_word_is_not_the_token(self):
        app = self.other_place()
        for cmd in ("reaper SteamLaunch AppId=%s0 -- x" % APPID,        # AppId=3891700
                    "reaper SteamLaunch AppId=%s -- x" % APPID[:-1],     # AppId=38917
                    "reaper SteamLaunch AppId=1%s -- x" % APPID,
                    "reaper SteamLaunch XAppId=%s -- x" % APPID,
                    "reaper SteamLaunch AppId=%sx -- x" % APPID,
                    "reaper SteamLaunch AppId= -- x",
                    "reaper SteamLaunch appid=%s -- x" % APPID):
            self.assertFalse(steam.is_running(app, [cmd]), cmd)

    def test_another_games_launcher_is_not_this_game(self):
        self.assertFalse(steam.is_running(self.other_place(), ["reaper SteamLaunch AppId=15100 -- x"]))

    def test_an_app_without_an_appid_keeps_the_old_behaviour(self):
        app = {"install_path": "/home/deck/.local/share/Steam/steamapps/common/Assassins Creed"}
        cmd = ["reaper SteamLaunch AppId=15100 -- /home/deck/.local/share/Steam/steamapps/common/"
               "Assassins Creed/AssassinsCreed_Dx9.exe"]
        self.assertTrue(steam.is_running(app, cmd))
        self.assertFalse(steam.is_running(app, ["reaper SteamLaunch AppId=15100 -- /elsewhere/x.exe"]))
        self.assertFalse(steam.is_running(app, ["/usr/bin/steam -gamepadui"]))


class AccentTest(GuardCase):
    def test_accented_folder_names_match_byte_for_byte_in_every_form(self):
        folder = self.app["install_path"]
        self.assertTrue(steam.is_running(self.app, posix_cmd(folder)))
        self.assertTrue(steam.is_running(self.app, windows_cmd(folder)))
        self.assertTrue(steam.is_running(self.app, posix_cmd(self.via_alias())))
        self.assertTrue(steam.is_running(self.app, windows_cmd(self.via_alias())))

    def test_there_is_no_unicode_normalisation(self):
        """The same bytes on both sides: a decomposed spelling is a different string (Linux keeps bytes)."""
        nfc, nfd = unicodedata.normalize("NFC", INSTALLDIR), unicodedata.normalize("NFD", INSTALLDIR)
        self.assertNotEqual(nfc, nfd)
        app = {"appid": "1", "install_path": "/x/steamapps/common/" + nfc}
        self.assertTrue(steam.is_running(app, ["/x/steamapps/common/%s/Game.exe" % nfc]))
        self.assertFalse(steam.is_running(app, ["/x/steamapps/common/%s/Game.exe" % nfd]))

    def test_a_proc_cmdline_with_accents_round_trips(self):
        proc = os.path.join(self.root, "proc")
        os.makedirs(os.path.join(proc, "42"))
        line = ("reaper SteamLaunch AppId=%s -- %s/DumaLegend.exe" % (APPID, self.via_alias())).encode("utf-8")
        with open(os.path.join(proc, "42", "cmdline"), "wb") as f:
            f.write(line.replace(b" ", b"\0"))
        self.assertTrue(steam.is_running(self.app, steam.running_install_paths(proc)))


class DriveLetterTest(GuardCase):
    """Proton logs the install folder as `<drive>:\\steamapps\\common\\<dir>\\...` (the library is mapped to its own
    letter: the verifier saw `S:\\steamapps\\common\\Serious Sam HD ...\\Bin\\SamHD.exe`): any letter, either slash, any case."""

    def cmd(self, text):
        return ["wine %s" % text]

    def test_the_form_the_verifier_found(self):
        self.assertTrue(steam.is_running(self.app, self.cmd("S:\\steamapps\\common\\%s\\Bin\\SamHD.exe" % INSTALLDIR)))

    def test_every_drive_letter_either_case(self):
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz":
            self.assertTrue(steam.is_running(self.app, self.cmd("%s:\\steamapps\\common\\%s\\Bin\\x.exe" % (letter, INSTALLDIR))),
                            letter)

    def test_either_slash_and_repeated_slashes(self):
        for text in ("S:/steamapps/common/%s/Bin/x.exe", "S:\\steamapps/common\\%s/Bin/x.exe",
                     "S:\\\\steamapps\\\\common\\\\%s\\\\Bin\\\\x.exe", "s:/steamapps\\common/%s\\x.exe"):
            self.assertTrue(steam.is_running(self.app, self.cmd(text % INSTALLDIR)), text)

    def test_case_does_not_matter_in_the_windows_forms(self):
        for text in ("S:\\STEAMAPPS\\COMMON\\%s\\Bin\\x.exe", "S:\\SteamApps\\Common\\%s\\x.exe"):
            self.assertTrue(steam.is_running(self.app, self.cmd(text % INSTALLDIR)), text)
        self.assertTrue(steam.is_running(self.app, self.cmd("S:\\steamapps\\common\\%s\\x.exe" % INSTALLDIR.upper())))
        self.assertTrue(steam.is_running(self.app, self.cmd("S:\\steamapps\\common\\%s\\x.exe" % INSTALLDIR.lower())))

    def test_quoted_and_inside_a_longer_command_line(self):
        self.assertTrue(steam.is_running(self.app, ['"C:\\windows\\system32\\start.exe" /exec "S:\\steamapps\\common\\%s\\Bin\\x.exe" -x'
                                                    % INSTALLDIR]))

    def test_the_sd_card_aliases_with_any_letter(self):
        app = {"appid": APPID, "install_path": os.path.join(self.common, INSTALLDIR)}
        for folder in (os.path.join(self.real_mount, "steamapps", "common", INSTALLDIR),
                       self.via_alias()):
            win = folder.replace("/", "\\")
            for letter in "DYz":
                self.assertTrue(steam.is_running(app, self.cmd("%s:%s\\Bin\\x.exe" % (letter, win))), (letter, folder))
        # and the library-relative form on the card
        self.assertTrue(steam.is_running(app, self.cmd("D:\\steamapps\\common\\%s\\Bin\\x.exe" % INSTALLDIR)))

    def test_the_old_z_forms_and_every_older_match_are_kept(self):
        for cmd in (posix_cmd(self.app["install_path"]), windows_cmd(self.app["install_path"]),
                    posix_cmd(self.via_alias()), windows_cmd(self.via_alias()),
                    ["reaper SteamLaunch AppId=%s -- /bin/true" % APPID]):
            self.assertTrue(steam.is_running(self.app, cmd), cmd)

    def test_other_games_longer_names_and_junk_are_not_matched(self):
        no = ("S:\\steamapps\\common\\Another Game\\Bin\\x.exe",
              "S:\\steamapps\\common\\%s Two\\Bin\\x.exe" % INSTALLDIR,
              "S:\\steamapps\\common\\Two %s\\x.exe" % INSTALLDIR,
              "S:\\steamapps\\common\\%s" % INSTALLDIR,                   # no separator after the folder name
              "1:\\steamapps\\common\\%s\\x.exe" % INSTALLDIR,           # not a drive letter
              "steamapps\\common\\%s\\x.exe" % INSTALLDIR,                # no drive at all
              "S:\\steamapps\\downloading\\%s\\x.exe" % INSTALLDIR,
              "S:\\other\\common\\%s\\x.exe" % INSTALLDIR)
        for text in no:
            self.assertFalse(steam.is_running(self.app, self.cmd(text)), text)

    def test_a_game_without_steamapps_in_its_path_only_matches_its_own_full_path(self):
        app = {"appid": APPID, "install_path": "/games/%s" % INSTALLDIR}
        self.assertTrue(steam.is_running(app, self.cmd("Y:\\games\\%s\\x.exe" % INSTALLDIR)))
        self.assertFalse(steam.is_running(app, self.cmd("Y:\\steamapps\\common\\%s\\x.exe" % INSTALLDIR)))

    def test_accents_survive(self):
        self.assertTrue(steam.is_running(self.app, self.cmd("S:\\steamapps\\common\\%s\\x.exe" % INSTALLDIR)))
        self.assertTrue(any(ord(c) > 127 for c in INSTALLDIR))


if __name__ == "__main__":
    unittest.main()
