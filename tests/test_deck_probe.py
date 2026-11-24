"""tools/deck_probe.py: the read-only Deck probe, run LOCALLY against a fake Deck tree, and its host mode.

The remote function takes the Deck's /proc, mountinfo, home and media root as parameters, so every case here
is a real run over a temp folder: a fake Steam library (libraryfolders.vdf, appmanifest), game files, a fake /proc
with command lines, a fake mountinfo, a fake plugin install. The host mode runs against a stub deck.sh, the way
.claude/tests/test_deck_scripts.py stubs ssh. No game data, no Deck.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import deck_probe as dp  # noqa: E402
import verify_fixtures as vf  # noqa: E402
from button_glyphs import steam  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402

APPID = "389170"
INSTALLDIR = "A Lenda do Herói - O Jogo"
PLUGIN = "Button Glyphs"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def tree_state(root):
    """Everything a read-only probe must not change: names, contents, sizes, modes and mtimes."""
    out = {}
    for base, dirs, names in os.walk(root):
        for n in dirs + names:
            p = os.path.join(base, n)
            st = os.lstat(p)
            digest = ""
            if stat.S_ISREG(st.st_mode) and st.st_size < (1 << 24):
                with open(p, "rb") as f:
                    digest = sha(f.read())
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mode, st.st_mtime_ns, digest)
    return out


class FakeDeck(unittest.TestCase):
    """~/ with a Steam library on a fake SD card, the game installed on it, a plugin install, a fake /proc."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        self.home = os.path.join(self.root, "home")
        self.media = os.path.join(self.root, "run", "media")
        self.sd = os.path.join(self.media, "deck", "LX512")
        self.alias = os.path.join(self.media, "LX512")
        self.proc = os.path.join(self.root, "proc")
        self.mountinfo = os.path.join(self.root, "mountinfo")
        steam_root = os.path.join(self.home, ".local", "share", "Steam")
        os.makedirs(os.path.join(steam_root, "steamapps"))
        with open(os.path.join(steam_root, "steamapps", "libraryfolders.vdf"), "w") as f:
            f.write('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
                    % (steam_root, self.sd))
        self.install = os.path.join(self.sd, "steamapps", "common", INSTALLDIR)
        os.makedirs(self.install)
        os.symlink(self.sd, self.alias)
        self.write_manifest()
        self.profile = vf.files_profiles(os.path.join(self.root, "art"))[APPID]
        self.entries = self.profile.files
        for e in self.entries:
            from test_files_patch import DRAWN, original_xnb
            w, h = next((w, h) for k, w, h in DRAWN if k == e["key"])
            self.put(e["file"], original_xnb(e["key"], w, h))
        os.makedirs(self.proc)
        self.mounts([("/", "ext4", "/dev/nvme0n1p8"), (self.sd, "ext4", "/dev/mmcblk0p1")])
        self.req = dp.build_request(self.profile, APPID, PLUGIN)
        self.install_plugin(dp.local_version() or "1.0.0")

    # ---- builders
    def write_manifest(self, flags="4", build="7053717", target=""):
        with open(os.path.join(self.sd, "steamapps", "appmanifest_%s.acf" % APPID), "w", encoding="utf-8") as f:
            f.write('"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"Songs for a Hero"\n\t"StateFlags"\t\t"%s"\n'
                    '\t"installdir"\t\t"%s"\n\t"buildid"\t\t"%s"\n\t"SizeOnDisk"\t\t"123456"\n%s}\n'
                    % (APPID, flags, INSTALLDIR, build, '\t"TargetBuildID"\t\t"%s"\n' % target if target else ""))

    def put(self, rel, data):
        path = os.path.join(self.install, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def mounts(self, rows):
        with open(self.mountinfo, "w") as f:
            for i, (mp, fstype, src) in enumerate(rows):
                f.write("%d 1 8:%d / %s rw,noatime shared:1 - %s %s rw,errors=continue\n" % (
                    100 + i, i, mp.replace(" ", "\\040"), fstype, src.replace(" ", "\\040")))

    def process(self, pid, cmd):
        os.makedirs(os.path.join(self.proc, str(pid)), exist_ok=True)
        with open(os.path.join(self.proc, str(pid), "cmdline"), "wb") as f:
            f.write(cmd.encode("utf-8").replace(b" ", b"\0"))

    def install_plugin(self, version, config=None):
        pdir = os.path.join(self.home, "homebrew", "plugins", PLUGIN)
        os.makedirs(pdir, exist_ok=True)
        with open(os.path.join(pdir, "package.json"), "w") as f:
            json.dump({"name": "button-glyphs", "version": version}, f)
        with open(os.path.join(pdir, "plugin.json"), "w") as f:
            json.dump({"name": PLUGIN, "flags": ["root"], "author": "x"}, f)
        cdir = os.path.join(self.home, "homebrew", "settings", PLUGIN)
        os.makedirs(cdir, exist_ok=True)
        self.config = config or {"version": 1, "games": {APPID: {"style": "ps", "last_event": "PlayStation applied",
                                                                  "backup": {"sha256": "SECRET-ish", "size": 1}},
                                                         "15100": {"style": "original", "last_event": ""}},
                                 "token": "do-not-print-me"}
        with open(os.path.join(cdir, "config.json"), "w") as f:
            json.dump(self.config, f)
        bdir = os.path.join(self.home, "homebrew", "data", PLUGIN, "backups", APPID, "files")
        os.makedirs(bdir, exist_ok=True)
        with open(os.path.join(bdir, "Tooltip_A.orig"), "wb") as f:
            f.write(b"x" * 100)

    def probe(self, req=None, **kw):
        with mock.patch.object(steam, "MEDIA_ROOT", self.media):
            return dp.remote_probe(req or self.req, steam, home=self.home, proc_root=self.proc,
                                   mountinfo=self.mountinfo, media_root=self.media, **kw)

    def rows(self, res):
        return {r["file"]: r for r in res["files"]["rows"]}


# ------------------------------------------------------------------------------------ remote mode


class RemoteProbeTest(FakeDeck):
    def test_a_healthy_game_reports_every_fact(self):
        res = self.probe()
        self.assertTrue(res["ok"])
        st = res["steam"]
        self.assertEqual((st["buildid"], st["StateFlags"], st["installdir"], st["SizeOnDisk"]),
                         ("7053717", 4, INSTALLDIR, "123456"))
        self.assertEqual(st["library"], self.sd)
        self.assertIn(self.sd, st["vdf_paths"])
        self.assertEqual(st["install_path"], self.install)
        self.assertEqual(res["files"]["match"], res["files"]["total"])
        self.assertEqual(res["files"]["total"], len(self.entries))
        self.assertEqual((res["fs"]["fstype"], res["fs"]["mount"]), ("ext4", self.sd))     # the longest mount wins
        self.assertEqual(res["fs"]["source"], "/dev/mmcblk0p1")
        self.assertGreater(res["fs"]["free_bytes"], 0)
        self.assertEqual(res["python"], sys.version.split()[0])
        self.assertIn(self.alias + "/steamapps/common/" + INSTALLDIR, st["samefile_aliases"])   # the other spelling, same folder
        self.assertIn("Content/Menu/xbox", [d["folder"] for d in res["dirs"]])
        self.assertTrue(all(r["owner"]["mode"] for r in res["files"]["rows"]))

    def test_match_differ_missing_not_regular_and_oversized(self):
        files = [e["file"] for e in self.entries]
        with open(os.path.join(self.install, files[0]), "r+b") as f:             # same size, one byte changed
            f.write(b"\x00")
        os.remove(os.path.join(self.install, files[1]))                          # missing
        victim = os.path.join(self.install, files[2])                            # a link to an outside copy
        outside = os.path.join(self.root, "outside.bin")
        shutil.copyfile(victim, outside)
        os.remove(victim)
        os.symlink(outside, victim)
        with open(os.path.join(self.install, files[3]), "wb") as f:              # a 3 GB sparse file where 2 KB belong
            f.truncate(3 << 30)
        t0 = time.time()
        res = self.probe()
        self.assertLess(time.time() - t0, 5)                                     # the sparse file was not read
        rows = self.rows(res)
        self.assertEqual(rows[files[0]]["state"], "differ")
        self.assertEqual(rows[files[1]]["state"], "missing")
        self.assertEqual((rows[files[2]]["state"], rows[files[2]]["detail"]), ("not-regular", "symlink"))
        self.assertEqual(rows[files[3]]["state"], "differ")
        self.assertIn("size", rows[files[3]]["detail"])
        lines = dp.judge(res, self.req)
        self.assertTrue(any(l[0] == "FAIL" and l[1] == "hashes" for l in lines))

    def test_a_folder_that_is_a_link_out_of_the_install_is_never_followed(self):
        linked = os.path.join(self.install, "Content", "Menu")
        moved = os.path.join(self.root, "menu-elsewhere")
        shutil.move(linked, moved)
        os.symlink(moved, linked)
        res = self.probe()
        menu = [r for r in res["files"]["rows"] if "/Menu/" in r["file"]]
        self.assertTrue(menu and all(r["state"] == "not-regular" and "link" in r["detail"] for r in menu))
        others = [r for r in res["files"]["rows"] if "/Menu/" not in r["file"]]
        self.assertTrue(all(r["state"] == "match" for r in others))

    def test_hashing_reads_at_most_the_pinned_size_plus_one_byte(self):
        reads = []
        real = os.read

        def spy(fd, n):
            data = real(fd, n)
            reads.append(len(data))
            return data

        rel = self.entries[0]["file"]
        with open(os.path.join(self.install, rel), "ab") as f:                  # a file that grew: size differs, never read
            f.write(b"z" * 10)
        with mock.patch.object(os, "read", spy):
            res = self.probe()
        self.assertEqual(self.rows(res)[rel]["state"], "differ")
        self.assertLessEqual(max(reads), 1 << 20)
        self.assertLessEqual(sum(reads), sum(e["size"] for e in self.entries) + len(self.entries))

    def test_processes_in_every_spelling_and_what_the_guard_says(self):
        via_alias = os.path.join(self.alias, "steamapps", "common", INSTALLDIR)
        self.process(101, "proton waitforexitandrun %s/DumaLegend.exe" % via_alias)
        self.process(102, "Z:" + (self.install + "/DumaLegend.exe").replace("/", "\\"))
        self.process(103, "reaper SteamLaunch AppId=%s -- /usr/bin/true" % APPID)
        self.process(104, "reaper SteamLaunch AppId=%s0 -- /usr/bin/true" % APPID)       # another game
        self.process(105, "/usr/bin/python3 -m http.server")
        res = self.probe()
        pids = {p["pid"]: p for p in res["running"]}
        self.assertEqual(sorted(pids), [101, 102, 103])
        self.assertTrue(all(p["guard"] for p in pids.values()))
        self.assertIn("AppId", pids[103]["why"])
        self.assertLessEqual(len(pids[101]["cmdline"]), dp.MAX_CMD)
        lines = dp.judge(res, self.req)
        self.assertTrue(any(l[1] == "running guard" and l[0] == "WARN" for l in lines))
        self.assertFalse(any(l[0] == "FAIL" for l in lines))

    def test_a_spelling_the_guard_does_not_know_is_a_fail(self):
        """The folder is mentioned through a mount name the guard cannot derive: the probe says so."""
        self.process(201, "proton run /mnt/odd-mount/steamapps/common/%s/DumaLegend.exe" % INSTALLDIR)
        res = self.probe()
        self.assertEqual([p["guard"] for p in res["running"]], [False])
        lines = dp.judge(res, self.req)
        self.assertTrue(any(l[0] == "FAIL" and l[1] == "running guard" for l in lines))

    def test_leftovers_unsafe_filesystem_owner_and_state_lines(self):
        self.put("Content/Tooltip/A.xnb" + dp.TMP_SUFFIX, b"half")
        self.put("DumaLegend.exe" + dp.TMP_SUFFIX, b"half")
        self.mounts([("/", "ext4", "/dev/root"), (self.sd, "exfat", "/dev/mmcblk0p1")])
        res = self.probe()
        self.assertEqual(res["leftovers"], ["Content/Tooltip/A.xnb" + dp.TMP_SUFFIX, "DumaLegend.exe" + dp.TMP_SUFFIX])
        lines = {l[1]: l for l in dp.judge(res, self.req)}
        self.assertEqual(lines["leftovers"][0], "WARN")
        self.assertEqual(lines["filesystem"][0], "WARN")
        self.assertIn("exfat", lines["filesystem"][2])
        self.mounts([("/", "ext4", "/dev/root"), (self.sd, "btrfs", "/dev/mmcblk0p1")])
        lines = {l[1]: l for l in dp.judge(self.probe(), self.req)}
        self.assertEqual(lines["filesystem"][0], "PASS")
        self.assertEqual(lines["leftovers"][0], "WARN")

    def test_mountinfo_paths_with_spaces_and_a_missing_file(self):
        self.mounts([("/", "ext4", "/dev/root"), (self.sd, "ext4", "/dev/sd a")])
        self.assertEqual(self.probe()["fs"]["source"], "/dev/sd a")
        os.remove(self.mountinfo)
        res = self.probe()
        self.assertIsNone(res["fs"]["fstype"])
        self.assertTrue(any(l[1] == "filesystem" and l[0] == "WARN" for l in dp.judge(res, self.req)))

    def test_state_flags_and_target_build_defer_note(self):
        self.write_manifest(flags="1026", build="7053717", target="7099999")
        res = self.probe()
        self.assertEqual((res["steam"]["StateFlags"], res["steam"]["TargetBuildID"]), (1026, "7099999"))
        self.assertTrue(any(l[1] == "steam state" and l[0] == "WARN" for l in dp.judge(res, self.req)))

    def test_plugin_config_is_filtered_and_hashed(self):
        res = self.probe()
        plugin = res["plugin"]
        self.assertEqual((plugin["installed"], plugin["flags"]), (True, ["root"]))
        self.assertEqual(plugin["game"], {"style": "ps", "last_event": "PlayStation applied"})
        self.assertEqual(plugin["games"]["15100"], {"style": "original", "last_event": ""})
        blob = json.dumps(res)
        for secret in ("do-not-print-me", "SECRET-ish"):
            self.assertNotIn(secret, blob)                                       # only styles and last_event leave the Deck
        with open(os.path.join(self.home, "homebrew", "settings", PLUGIN, "config.json"), "rb") as f:
            self.assertEqual(plugin["config_sha256"], hashlib.sha256(f.read()).hexdigest())
        self.assertEqual((plugin["backups"]["exists"], plugin["backups"]["files"], plugin["backups"]["bytes"]), (True, 1, 100))
        lines = {l[1]: l for l in dp.judge(res, self.req, local_version=plugin["version"])}
        self.assertEqual(lines["plugin"][0], "PASS")
        lines = {l[1]: l for l in dp.judge(res, self.req, local_version="9.9.9")}
        self.assertEqual(lines["plugin"][0], "WARN")
        shutil.rmtree(os.path.join(self.home, "homebrew", "plugins"))
        self.assertFalse(self.probe()["plugin"]["installed"])

    def test_pending_style_with_original_files_is_flagged(self):
        lines = {l[1]: l for l in dp.judge(self.probe(), self.req, local_version=dp.local_version())}
        self.assertEqual(lines["plugin state"][0], "WARN")                       # style ps chosen, files still the pinned ones
        self.assertIn("pending", lines["plugin state"][2])

    def test_a_game_that_is_not_installed(self):
        os.remove(os.path.join(self.sd, "steamapps", "appmanifest_%s.acf" % APPID))
        res = self.probe()
        self.assertFalse(res["ok"])
        lines = dp.judge(res, self.req)
        self.assertEqual(lines[0][:2], ("FAIL", "install"))

    def test_nothing_on_the_fake_deck_is_ever_changed(self):
        self.process(301, "reaper SteamLaunch AppId=%s -- x" % APPID)
        self.put("Content/Tooltip/A.xnb" + dp.TMP_SUFFIX, b"half")
        before = tree_state(self.root)
        self.probe()
        self.assertEqual(tree_state(self.root), before)

    def test_the_remote_text_has_no_write_primitive(self):
        import ast
        tree = ast.parse(dp.remote_source())
        banned_on_os = {"remove", "unlink", "rename", "replace", "chmod", "chown", "makedirs", "mkdir", "rmdir", "symlink",
                        "link", "truncate", "system", "popen", "write", "ftruncate", "utime", "mkfifo"}
        banned_anywhere = {"write", "writelines", "truncate"}
        problems = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) else [])
                problems += [n for n in names if n and n.split(".")[0] in ("shutil", "subprocess", "tempfile", "ctypes")]
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and (f.attr in banned_anywhere or (
                        isinstance(f.value, ast.Name) and f.value.id == "os" and f.attr in banned_on_os)):
                    problems.append("call to ." + f.attr)
                if isinstance(f, ast.Name) and f.id == "open":
                    mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
                    if mode is not None and not (isinstance(mode, ast.Constant) and set(mode.value) <= set("rb")):
                        problems.append("open() with a mode that can write")
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os" \
                    and node.attr in ("O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND"):
                problems.append("os." + node.attr)
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and "sudo" in node.value:
                problems.append("sudo")
        self.assertEqual(problems, [])

    def test_the_assembled_script_runs_standalone_and_matches_the_direct_call(self):
        self.process(401, "reaper SteamLaunch AppId=%s -- x" % APPID)
        script = dp.build_script(self.req)
        env = {"PATH": os.environ.get("PATH", ""), "DECK_PROBE_HOME": self.home, "DECK_PROBE_PROC": self.proc,
               "DECK_PROBE_MOUNTINFO": self.mountinfo, "DECK_PROBE_MEDIA": self.media}
        done = subprocess.run([sys.executable, "-"], input=script, capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        remote = dp.parse_result(done.stdout)
        direct = self.probe()
        for res in (remote, direct):
            res.pop("seconds")
        self.assertEqual(remote, json.loads(json.dumps(direct)))
        self.assertEqual(len(done.stdout.strip().splitlines()), 1)               # ONE JSON object

    def test_a_remote_failure_is_still_one_json_object(self):
        script = dp.build_script(self.req, steam_source="raise RuntimeError('boom')")
        done = subprocess.run([sys.executable, "-"], input=script, capture_output=True, text=True, timeout=60)
        res = dp.parse_result(done.stdout)
        self.assertEqual((res["ok"], "boom" in res["error"]), (False, True))


class RemoteRegionsTest(FakeDeck):
    """A giant archive: only the pinned byte ranges are read."""

    def setUp(self):
        super().setUp()
        import test_regions as trg
        self.regions_profile = vf.regions_profiles()[vf.REGIONS_APPID]
        self.textures = [trg.texture(7), trg.texture(9)]
        arch = os.path.join(self.install, self.regions_profile.file)
        os.makedirs(os.path.dirname(arch), exist_ok=True)
        end = max(r["offset"] + r["size"] for r in self.regions_profile.regions)
        with open(arch, "wb") as f:
            f.truncate(end + 1000)
            for r, t in zip(self.regions_profile.regions, self.textures):
                f.seek(r["offset"])
                f.write(t)
        self.arch = arch
        self.rreq = dp.build_request(self.regions_profile, APPID, PLUGIN)

    def test_ranges_match_and_a_patched_region_is_recognised_by_its_marker(self):
        res = self.probe(self.rreq)
        self.assertEqual((res["regions"]["match"], res["regions"]["total"], res["regions"]["marked"]), (2, 2, 0))
        with open(self.arch, "r+b") as f:
            r = self.regions_profile.regions[1]
            f.seek(r["offset"] + 0x20)
            f.write(b"BGLYPHS\x01")
            f.seek(r["offset"] + 5000)
            f.write(b"\x99")
        res = self.probe(self.rreq)
        self.assertEqual((res["regions"]["match"], res["regions"]["marked"]), (1, 1))
        lines = {l[1]: l for l in dp.judge(res, self.rreq)}
        self.assertEqual(lines["regions"][0], "WARN")                            # patched by us, not a different build
        self.assertIn("patched", lines["regions"][2])

    def test_a_different_region_without_the_marker_is_a_fail_and_budget_skips(self):
        with open(self.arch, "r+b") as f:
            f.seek(self.regions_profile.regions[0]["offset"] + 3000)
            f.write(b"\x42")
        res = self.probe(self.rreq)
        lines = {l[1]: l for l in dp.judge(res, self.rreq)}
        self.assertEqual(lines["regions"][0], "FAIL")
        small = dict(self.rreq, max_bytes=100)
        res = self.probe(small)
        self.assertEqual({r["state"] for r in res["regions"]["rows"]}, {"skipped"})

    def test_a_truncated_archive_and_a_link_to_an_archive_elsewhere(self):
        with open(self.arch, "r+b") as f:
            f.truncate(self.regions_profile.regions[0]["offset"] + 10)
        res = self.probe(self.rreq)
        self.assertEqual({r["state"] for r in res["regions"]["rows"]}, {"differ"})
        elsewhere = os.path.join(self.root, "elsewhere.rmdp")
        shutil.move(self.arch, elsewhere)
        os.symlink(elsewhere, self.arch)
        res = self.probe(self.rreq)
        self.assertEqual(res["regions"]["archive"]["state"], "not-regular")


# ------------------------------------------------------------------------------------ the request from a real profile


class RequestTest(unittest.TestCase):
    def test_files_request_has_the_profiles_files_and_hashes(self):
        profile = PROFILES["389170"]
        req = dp.build_request(profile, "389170")
        self.assertEqual(len(req["files"]), len(profile.files))
        self.assertEqual([(f["file"], f["size"], f["sha256"]) for f in req["files"]],
                         [(e["file"], e["size"], e["sha256"]) for e in profile.files])

    def test_regions_request_has_the_archive_and_every_range(self):
        profile = PROFILES["474960"]
        req = dp.build_request(profile, "474960")
        self.assertEqual(req["archive"], profile.file)
        self.assertEqual([(r["key"], r["offset"], r["size"], r["sha256"]) for r in req["regions"]],
                         [(r["key"], r["offset"], r["size"], r["sha256"]) for r in profile.regions])
        self.assertEqual(req["files"], [])
        self.assertEqual(dp.build_request(PROFILES["348710"], "348710")["archive"], "localize.cpk")

    def test_the_other_kinds(self):
        ninja = dp.build_request(PROFILES["435790"], "435790")
        build = next(iter(PROFILES["435790"].builds.values()))
        self.assertEqual(ninja["files"], [{"file": build["file"], "size": build["size"], "sha256": build["sha256"]}])
        self.assertEqual(ninja["save"], PROFILES["435790"].file)
        ac1 = dp.build_request(PROFILES["15100"], "15100")
        self.assertEqual([f["file"] for f in ac1["files"]], [t["file"] for t in PROFILES["15100"].targets()])
        self.assertTrue(all(f["sha256"] is None for f in ac1["files"]))
        self.assertEqual(dp.build_request(PROFILES["405290"], "405290")["files"][0]["file"], "CharlieMurder.exe")

    def test_the_request_is_plain_json_and_names_no_absolute_path(self):
        for appid, profile in PROFILES.items():
            blob = json.dumps(dp.build_request(profile, appid), sort_keys=True)
            self.assertNotIn("/home/", blob)
            self.assertNotIn("steamapps", blob)
            self.assertEqual(json.loads(blob)["appid"], appid)


# ------------------------------------------------------------------------------------ host mode with a stub deck.sh


class HostModeTest(FakeDeck):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.root, "stub.log")
        self.stdin = os.path.join(self.root, "stub.stdin")
        self.canned = os.path.join(self.root, "stub.json")
        self.stub = os.path.join(self.root, "deck.sh")
        with open(self.stub, "w") as f:
            f.write('#!/bin/bash\nprintf "%s\\n" "$*" >> "$STUB_LOG"\n'
                    'case "$1" in\n  --check) exit "${STUB_CHECK_RC:-0}";;\n  true) exit 0;;\n'
                    '  "python3 -") cat > "$STUB_STDIN"; cat "$STUB_JSON";;\n  *) exit 64;;\nesac\n')
        os.chmod(self.stub, 0o755)
        self.env = {"DECK_SH": self.stub, "STUB_LOG": self.log, "STUB_STDIN": self.stdin, "STUB_JSON": self.canned}

    def canned_from(self, res):
        with open(self.canned, "w") as f:
            f.write("some banner the Deck printed\n" + json.dumps(res) + "\n")

    def run_main(self, *argv, env=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, dict(self.env, **(env or {}))), \
                mock.patch.dict(PROFILES, {APPID: self.profile}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = dp.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return f.read().splitlines()

    def test_all_good_exit_zero_and_the_command_names_no_game_path(self):
        self.canned_from(self.probe())
        path = os.path.join(self.root, "out.json")
        code, out, err = self.run_main(APPID, "--json", path)
        self.assertEqual(code, 0, (out, err))
        self.assertIn("PASS  hashes", out)
        self.assertIn("RESULT:", out)
        self.assertEqual(self.calls(), ["--check", "true", "python3 -"])            # the command text is only this
        with open(self.stdin) as f:
            script = f.read()
        self.assertIn("def remote_probe(", script)
        self.assertIn(self.entries[0]["sha256"], script)                           # the request travels in the script
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(set(data), {"request", "remote", "checks", "verdict"})
        self.assertEqual(data["request"]["appid"], APPID)

    def test_a_failure_gives_exit_one(self):
        with open(os.path.join(self.install, self.entries[0]["file"]), "r+b") as f:
            f.write(b"\x00")
        self.canned_from(self.probe())
        code, out, _ = self.run_main(APPID)
        self.assertEqual(code, 1)
        self.assertIn("FAIL  hashes", out)
        self.assertIn("RESULT: FAIL", out)

    def test_unreachable_deck_is_exit_two_and_nothing_else_is_called(self):
        code, out, err = self.run_main(APPID, env={"STUB_CHECK_RC": "1"})
        self.assertEqual(code, 2)
        self.assertIn("Deck unreachable", err)
        self.assertEqual(self.calls(), ["--check"])

    def test_dry_run_prints_the_request_without_connecting(self):
        code, out, _ = self.run_main(APPID, "--dry-run-request", env={"DECK_SH": "/nonexistent/deck.sh"})
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), json.loads(json.dumps(dp.build_request(self.profile, APPID))))
        self.assertEqual(self.calls(), [])

    def test_unknown_appid_and_garbage_from_the_deck(self):
        self.assertEqual(self.run_main("99999")[0], 1)
        with open(self.canned, "w") as f:
            f.write("Traceback (most recent call last): nothing\n")
        code, _, err = self.run_main(APPID)
        self.assertEqual(code, 1)
        self.assertIn("without a JSON object", err)

    def test_a_remote_error_object_is_a_fail_line(self):
        self.canned_from({"schema": 1, "ok": False, "error": "RuntimeError: boom"})
        code, out, _ = self.run_main(APPID)
        self.assertEqual(code, 1)
        self.assertIn("boom", out)


class JudgeTest(unittest.TestCase):
    """judge() on canned results: each rule on its own."""

    def base(self):
        return {"ok": True, "steam": {"installed": True, "name": "G", "buildid": "1", "installdir": "g", "library": "/x",
                                      "StateFlags": 4, "TargetBuildID": "", "guard_forms": ["/x/steamapps/common/g/"]},
                "files": {"rows": [{"file": "a", "state": "match", "owner": {"user": "deck", "group": "deck", "mode": "644"}}]},
                "fs": {"fstype": "ext4", "mount": "/x", "options": "rw", "free_bytes": 50 << 30},
                "running": [], "leftovers": [], "me": "deck", "loader": {"uid": 1000, "user": "deck"},
                "plugin": {"installed": True, "version": "1", "flags": [], "game": {"style": "original"}, "name": "P"}}

    def req(self):
        return {"files": [{"file": "a", "size": 1, "sha256": "x"}], "regions": []}

    def levels(self, res):
        return {l[1]: l[0] for l in dp.judge(res, self.req(), "1")}

    def test_healthy(self):
        self.assertEqual({v for v in self.levels(self.base()).values()}, {"PASS"})

    def test_owner_other_than_the_plugin_user_warns(self):
        res = self.base()
        res["loader"] = {"uid": 0, "user": "root"}
        self.assertEqual(self.levels(res)["owner"], "WARN")

    def test_space_levels(self):
        res = self.base()
        res["fs"]["free_bytes"] = 10 << 20
        self.assertEqual(self.levels(res)["free space"], "FAIL")
        res["fs"]["free_bytes"] = 500 << 20
        self.assertEqual(self.levels(res)["free space"], "WARN")

    def test_read_only_mount_and_error_recorded_by_the_plugin_fail(self):
        res = self.base()
        res["fs"]["options"] = "ro,noatime"
        res["plugin"]["game"] = {"style": "ps", "error": "nope"}
        lv = self.levels(res)
        self.assertEqual((lv["filesystem"], lv["plugin state"]), ("FAIL", "FAIL"))


if __name__ == "__main__":
    unittest.main()
