"""tools/serious_build.py: facts from synthetic .gro archives, the generated table, and what the Deck side may do.

Synthetic fixtures only (tests/croteam_fixture.py builds Croteam-shaped .tex files; gro.build writes the archives). The
committed profiles/serious_tables.py is checked for shape; it is compared with a fresh run only when a facts folder from a
real run is named in SERIOUS_FACTS_DIR (facts_<appid>.json, game bytes: keep it outside the repo).
"""
import ast
import builtins
import hashlib
import importlib.util
import io
import os
import struct
import sys
import tempfile
import unittest
import zlib
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)

from button_glyphs import gro  # noqa: E402
import croteam_fixture as F  # noqa: E402
import serious_build as SB  # noqa: E402

BASE = "Content/SeriousSamHD/Textures/Menu/"
ATLAS = "Content/SeriousSamHD/Textures/GlobalAtlas.rsc"
TABLES = os.path.join(ROOT, "py_modules", "button_glyphs", "profiles", "serious_tables.py")


def dxt_tex(w, h, colour, stamp=b"$Version: Synthetic HD; test"):
    blocks = []
    for lw, lh in F.mips(w, h):
        blocks += [F.dxt5_flat_block(colour)] * (max(1, (lw + 3) // 4) * max(1, (lh + 3) // 4))
    return F.build_hd(w, h, 10, F.hd_dxt_block(blocks), stamp=stamp)


def rgba_tex(w, h, seed):
    pic = F.pattern(w, h, seed)
    return F.build_hd(w, h, 5, F.hd_rgba_block(F.rgba_chain(pic, w, h)))


def atlas_bytes():
    """Head + two parts (A at x=0, B at x=0.5) + one 16x8 DXT5 pixel block + tail, the shape of a CStaticAtlas."""
    parts = b""
    for name, fl in (("Content/SeriousSamHD/Textures/Menu/XBoxButtonA.tex", (0.5, 1.0, 0.0, 0.0)),
                     ("Content/SeriousSamHD/Textures/Menu/XBoxButtonB.tex", (0.5, 1.0, 0.5, 0.0))):
        raw = name.encode()
        parts += struct.pack("<I", len(raw)) + raw + struct.pack("<ffff", *fl)
    blocks = []
    for lw, lh in F.mips(16, 8):
        blocks += [F.dxt5_flat_block(0x07E0)] * (max(1, (lw + 3) // 4) * max(1, (lh + 3) // 4))
    block = F.hd_dxt_block(blocks)
    return (F.head(b"$Version: Synthetic atlas") + parts + struct.pack("<II", 16, 8) + b"\x01\x02\x00\x08" + struct.pack("<I", 10)
            + b"STAR" + struct.pack("<I", len(block)) + block + F.TAIL_HD_DXT)


def make_game():
    spec = [
        (BASE + "XBoxButtonA.tex", "a", "button", "replace", "a", "test"),
        (BASE + "XBoxButtonB.tex", "b", "button", "replace", "b", "test"),
        (BASE + "A_button.tex", "a", "button", "replace", "a", "test"),
    ]
    return {"title": "Synthetic", "short": "tfe", "build": "111", "mount": "Content/SeriousSamHD", "log": "Log/Test.log",
            "exe": "Bin/None.exe", "spec": spec, "atlas": ATLAS, "not_touched": [(BASE + "PC_Enter.tex", "keycap")]}


class Install:
    """A synthetic install folder: All.gro (+ a later All_2.gro that overrides A_button) and a log naming both."""

    def __init__(self, root, swap_log=False):
        self.root = root
        self.files = {
            BASE + "XBoxButtonA.tex": dxt_tex(8, 8, 0x07E0), BASE + "XBoxButtonB.tex": dxt_tex(8, 8, 0xF800),
            BASE + "A_button.tex": rgba_tex(8, 8, 1), BASE + "PC_Enter.tex": rgba_tex(8, 8, 2),
            BASE + "XBoxButtonA.rsc": b"wrapper-a", ATLAS: atlas_bytes(),
            "Content/SeriousSamHD/Databases/MenuPalette.rsc": b"CTSEMETA..." + b"Content/SeriousSamHD/Textures/Menu/XBoxButtonB.tex" + b"\0",
        }
        self.override = rgba_tex(8, 8, 9)
        d = os.path.join(root, "Content", "SeriousSamHD")
        os.makedirs(d)
        os.makedirs(os.path.join(root, "Log"))
        e1 = list(self.files.items())
        e2 = [(BASE + "A_button.tex", self.override)]
        with open(os.path.join(d, "All.gro"), "wb") as f:
            f.write(gro.build(e1))
        with open(os.path.join(d, "All_2.gro"), "wb") as f:
            f.write(gro.build(e2))
        order = [("All.gro", len(e1)), ("All_2.gro", len(e2))]
        if swap_log:
            order.reverse()
        with open(os.path.join(root, "Log", "Test.log"), "w") as f:
            f.write("LOG:   Loading group files...\n")
            for n, c in order:
                f.write("LOG:     Content/SeriousSamHD/%s: %d files, signature: OFFICIAL\n" % (n, c))


def facts_for(inst, game):
    return SB.collect(SB.make_request(game, "1"), inst.root, "111")


class FactsAndTableTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.inst = Install(self.tmp.name)
        self.game = make_game()
        self.facts = facts_for(self.inst, self.game)

    def rows(self):
        rows, _ = SB.analyse(self.game, self.facts, "1")
        return dict((r["path"].rsplit("/", 1)[-1], r) for r in rows)

    def test_sorted_order_matches_the_games_own_log(self):
        self.assertEqual([g["name"] for g in self.facts["gros"]], ["All.gro", "All_2.gro"])
        self.assertTrue(self.facts["log_matches_sorted_order"] and self.facts["log_entry_counts_match"])

    def test_the_last_archive_wins_and_layers_are_listed(self):
        r = self.rows()
        self.assertEqual((r["A_button.tex"]["gro"], r["A_button.tex"]["layers"]), ("All_2.gro", ["All.gro", "All_2.gro"]))
        self.assertEqual((r["XBoxButtonA.tex"]["gro"], r["XBoxButtonA.tex"]["layers"]), ("All.gro", ["All.gro"]))
        # the pin is the WINNER's bytes, not the shadowed copy's
        self.assertEqual(r["A_button.tex"]["sha256"], hashlib.sha256(self.inst.override).hexdigest())
        self.assertNotEqual(r["A_button.tex"]["sha256"], hashlib.sha256(self.inst.files[BASE + "A_button.tex"]).hexdigest())

    def test_row_fields_come_from_the_file(self):
        r = self.rows()["XBoxButtonA.tex"]
        data = self.inst.files[BASE + "XBoxButtonA.tex"]
        self.assertEqual((r["width"], r["height"], r["fmt"], r["fmt_code"], r["mips"], r["writable"]), (8, 8, "dxt5", 10, 4, True))
        self.assertEqual((r["size"], r["sha256"], r["crc32"]), (len(data), hashlib.sha256(data).hexdigest(), "%08x" % (zlib.crc32(data) & 0xFFFFFFFF)))
        self.assertEqual(r["wrapper"]["gro"], "All.gro")
        self.assertEqual(self.rows()["A_button.tex"]["fmt"], "rgba8")

    def test_atlas_parts_are_measured_and_must_match_the_texture_size(self):
        rows, _ = SB.analyse(self.game, self.facts, "1")
        atlas = [r for r in rows if r["kind"] == "atlas"][0]
        self.assertEqual(atlas["atlas_textures"][0][:4], (16, 8, "dxt5", 5))
        self.assertEqual(atlas["atlas_parts"], [("SeriousSamHD/Textures/Menu/XBoxButtonA", "a", 0, [0, 0, 8, 8]),
                                                ("SeriousSamHD/Textures/Menu/XBoxButtonB", "b", 0, [8, 0, 8, 8])])
        self.assertEqual(self.rows()["XBoxButtonB.tex"]["atlas_part"], {"group": 0, "rect": [8, 0, 8, 8]})
        self.assertEqual(self.rows()["XBoxButtonB.tex"]["referenced_by"],       # resources of the winning layers that name it
                         ["All.gro:Content/SeriousSamHD/Databases/MenuPalette.rsc", "All.gro:Content/SeriousSamHD/Textures/GlobalAtlas.rsc"])
        # a part whose rectangle is not the texture's size refuses the table
        bad = dict(self.game, spec=[(BASE + "A_button.tex", "a", "button", "replace", "a", "")])
        bad["atlas"] = ATLAS
        facts = dict(self.facts)
        facts["listing"] = dict(self.facts["listing"])
        with mock.patch.object(SB, "atlas_info", lambda d: ([{"width": 16, "height": 8, "fmt": "dxt5", "fmt_code": 10, "mips": 5, "offset": 0, "bytes": 1}],
                                                            [{"path": BASE + "A_button.tex", "group": 0, "rect": [0, 0, 4, 4]}])):
            with self.assertRaises(SB.TableError):
                SB.analyse(bad, facts, "1")

    def test_the_module_is_deterministic_marked_generated_and_has_no_game_bytes(self):
        tables, _, problems = SB.tables_from_facts({"1": self.facts}, {"1": self.game})
        self.assertEqual(problems, [])
        text = SB.render_module(tables, {"a 8x8": ("a.svg.x", "resize")})
        self.assertEqual(text, SB.render_module(tables, {"a 8x8": ("a.svg.x", "resize")}))
        self.assertIn("generated by tools/serious_build.py", text[:200].lower())
        ns = {}
        exec(compile(text, "t", "exec"), ns)
        t = ns["GAMES"]["1"]
        self.assertEqual((t["build"], [g[0] for g in t["load_order"]]), ("111", ["All.gro", "All_2.gro"]))
        for raw in list(self.inst.files.values()) + [self.inst.override]:
            self.assertNotIn(raw[:48].hex(), text)       # no pixel bytes, only hashes
        self.assertTrue(all(len(r["sha256"]) == 64 and len(r["crc32"]) == 8 for r in t["textures"]))

    def test_a_missing_texture_a_wrong_build_or_a_swapped_log_refuses_the_table(self):
        miss = dict(self.game, spec=self.game["spec"] + [(BASE + "Nope.tex", "x", "button", "replace", "x", "")])
        with self.assertRaises(SB.TableError):
            SB.analyse(miss, facts_for(self.inst, miss), "1")
        wrong = SB.collect(SB.make_request(self.game, "1"), self.inst.root, "222")
        with self.assertRaises(SB.TableError):
            SB.analyse(self.game, wrong, "1")
        with tempfile.TemporaryDirectory() as t2:
            swapped = Install(t2, swap_log=True)
            with self.assertRaises(SB.TableError) as cm:
                SB.analyse(self.game, facts_for(swapped, self.game), "1")
            self.assertIn("ambiguous", str(cm.exception))
        _, _, problems = SB.tables_from_facts({"1": SB.collect(SB.make_request(self.game, "1"), self.inst.root, "222")}, {"1": self.game})
        self.assertTrue(problems)

    def test_bytes_that_disagree_with_the_central_directory_are_refused(self):
        facts = dict(self.facts)
        facts["fetched"] = dict(self.facts["fetched"])
        key = (BASE + "XBoxButtonA.tex").lower()
        bad = dict(facts["fetched"][key])
        import base64
        bad["z"] = base64.b64encode(zlib.compress(b"not the file")).decode()
        facts["fetched"][key] = bad
        with self.assertRaises(SB.TableError):
            SB.analyse(self.game, facts, "1")

    def test_unlisted_candidates_exclude_the_table_and_the_excused(self):
        extra = dict(self.facts)
        extra["listing"] = dict(self.facts["listing"])
        extra["listing"][(BASE + "XBoxButtonZ.tex").lower()] = [[0, BASE + "XBoxButtonZ.tex", 10, 5, 8, 0, 0]]
        extra["listing"][(BASE + "Htp_Use_06.tex").lower()] = [[0, BASE + "Htp_Use_06.tex", 10, 5, 8, 0, 0]]
        got = [p for p, _, _ in SB.unlisted_candidates(self.game, extra)]
        self.assertEqual(got, [BASE + "XBoxButtonZ.tex"])


class LayoutTest(unittest.TestCase):
    def fake(self, names, rects, skew=0):
        head = b"CTSEMETA" + b"".join(n.encode() + b"\0" for n in names) + b"EXTY" + b"junk" * 3 + b"OBJS" + b"\x01\x02"
        body = b""
        for i, (klass, serial, r) in enumerate(rects):
            body += b"\x00" * ((i + skew) % 3) + struct.pack("<ii", klass, serial) + struct.pack("<ffff", *r) + b"DTRS\x05\0\0\0"
        return head + body + b"\x00" * 32

    def test_rectangles_are_read_at_unaligned_offsets_in_order(self):
        names = ["imgLogo", "lblLTArrowHorizontal", "arrArranger"]
        rects = [(15, 2, [370.0, 15.0, 225.0, 75.0]), (32, 5, [136.0, 254.0, 160.0, 18.0]), (38, 6, [120.0, 106.0, 256.0, 32.0])]
        data = self.fake(names, rects)
        got = SB.layout_rects(data)
        self.assertEqual([(g[0], g[1], g[2], g[4]) for g in got], [(n, k, i, r) for n, (k, i, r) in zip(names, rects)])
        for _, _, _, off, r in got:
            self.assertEqual(list(struct.unpack_from("<ffff", data, off)), r)       # the offset really points at the four floats

    def test_a_count_that_does_not_match_the_names_is_refused_not_guessed(self):
        names = ["imgLogo", "lblA", "lblB"]
        data = self.fake(names, [(15, 2, [1.0, 2.0, 3.0, 4.0]), (32, 5, [5.0, 6.0, 7.0, 8.0])])
        self.assertIsNone(SB.layout_rects(data))
        self.assertIsNone(SB.layout_rects(b"CTSEMETA"))


class DeckSideTest(unittest.TestCase):
    """The code sent to the Deck reads; it never writes, deletes or runs anything."""

    FORBIDDEN_ATTRS = {"remove", "unlink", "rename", "rmdir", "mkdir", "makedirs", "write", "writelines", "truncate", "chmod",
                       "system", "popen", "Popen", "run", "symlink", "link", "utime", "rmtree", "copy", "move", "setxattr"}
    ALLOWED_IMPORTS = {"base64", "hashlib", "json", "os", "re", "sys", "time", "types", "zipfile", "zlib"}

    def test_remote_source_has_no_write_primitive_and_only_stdlib_imports(self):
        tree = ast.parse(SB.remote_source())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertTrue({a.name.split(".")[0] for a in node.names} <= self.ALLOWED_IMPORTS, ast.dump(node))
            if isinstance(node, ast.ImportFrom):
                self.fail("from-import in the Deck code: %s" % node.module)
            if isinstance(node, ast.Attribute):
                self.assertNotIn(node.attr, self.FORBIDDEN_ATTRS, "line %d" % node.lineno)
                if node.attr == "replace":       # str.replace is fine, os.replace is not
                    self.assertNotEqual(getattr(node.value, "id", ""), "os", "os.replace at line %d" % node.lineno)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                mode = node.args[1].value if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "r"
                self.assertEqual(set(mode) - {"r", "b"}, set(), "open() with mode %r at line %d" % (mode, node.lineno))
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("eval", "__import__", "input"):
                self.fail("dynamic code in the Deck part")

    def test_the_script_names_no_game_path_and_ends_with_one_call(self):
        script = SB.build_script({"appid": "41000", "mount": "x"}, steam_source="X = 1", remote="def remote_main(a, b):\n    pass\n")
        self.assertTrue(script.rstrip().endswith("')") or "remote_main(" in script.splitlines()[-2] + script.splitlines()[-1])
        self.assertNotIn("/home/deck", script)

    def test_reads_are_bounded_a_big_unrelated_entry_is_never_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            inst = Install(tmp)
            big = os.path.join(tmp, "Content", "SeriousSamHD", "All_9.gro")
            with open(big, "wb") as f:
                f.write(gro.build([("Content/SeriousSamHD/Models/Huge.bin", os.urandom(1 << 20) * 24)]))   # 24 MB, incompressible name
            game = make_game()
            req = SB.make_request(game, "1")
            seen = {}
            real_open = io.open

            class Counting(object):
                def __init__(self, fp, path):
                    self._fp, self._path = fp, path

                def read(self, n=-1):
                    d = self._fp.read(n)
                    seen[self._path] = seen.get(self._path, 0) + len(d)
                    return d

                def __getattr__(self, name):
                    return getattr(self._fp, name)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    self._fp.close()

            def spy(path, *a, **k):
                fp = real_open(path, *a, **k)
                return Counting(fp, str(path)) if str(path).endswith(".gro") else fp

            with mock.patch("zipfile.io.open", spy), mock.patch.object(builtins, "open", spy):
                SB.collect(req, tmp, "111")
            self.assertTrue(seen)
            self.assertLess(max(seen.values()), 2 << 20, seen)      # 24 MB entry, under 2 MB read from any archive


class CommittedTableTest(unittest.TestCase):
    @unittest.skipUnless(os.path.exists(TABLES), "profiles/serious_tables.py is not generated yet")
    def test_shape(self):
        spec = importlib.util.spec_from_file_location("serious_tables", TABLES)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with open(TABLES, encoding="utf-8") as f:
            self.assertIn("generated by tools/serious_build.py", f.read(300).lower())
        self.assertEqual(sorted(mod.GAMES), ["204340", "41000", "41010"])
        for appid, g in mod.GAMES.items():
            names = [x[0] for x in g["load_order"]]
            ids = [r["id"] for r in g["textures"]]
            self.assertEqual(len(ids), len(set(ids)), appid)
            for r in g["textures"]:
                for k in SB.ROW_KEYS:
                    self.assertIn(k, r, (appid, r["id"]))
                self.assertRegex(r["sha256"], "^[0-9a-f]{64}$")
                self.assertRegex(r["crc32"], "^[0-9a-f]{8}$")
                self.assertIn(r["gro"], names)
                self.assertEqual(r["layers"][-1], r["gro"])
                self.assertTrue(set(r["layers"]) <= set(names))
                self.assertGreater(r["size"], 0)
                if r["kind"] != "layout" and r["fmt"] != "unparsed":
                    self.assertGreater(r["width"], 0)
                if r["kind"] == "layout":
                    self.assertTrue(all(len(g[4]) == 4 for g in r["layout"]))
                if r["kind"] == "atlas":
                    tex = r["atlas_textures"]
                    for _id, _role, group, (x, y, w, h) in r["atlas_parts"]:
                        self.assertLessEqual(x + w, tex[group][0])
                        self.assertLessEqual(y + h, tex[group][1])

    @unittest.skipUnless(os.environ.get("SERIOUS_FACTS_DIR") and os.path.exists(TABLES),
                         "set SERIOUS_FACTS_DIR (facts_<appid>.json from `serious_build.py build --facts-out`) to compare with the committed table")
    def test_a_fresh_run_equals_the_committed_table(self):
        import json
        facts = {}
        for appid in sorted(SB.GAMES):
            with open(os.path.join(os.environ["SERIOUS_FACTS_DIR"], "facts_%s.json" % appid)) as f:
                facts[appid] = json.load(f)
        tables, _, problems = SB.tables_from_facts(facts)
        self.assertEqual(problems, [])
        fresh = SB.render_module(tables, {})
        with open(TABLES, encoding="utf-8") as f:
            committed = f.read()
        cut = lambda t: t[:t.index("CATALOG = {")]       # noqa: E731 - the catalog snapshot follows catalog.json, not the games
        self.assertEqual(cut(fresh), cut(committed))


if __name__ == "__main__":
    unittest.main()
