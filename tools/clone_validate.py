#!/usr/bin/env python3
"""Validate a profile on the game's REAL files, without risking them.

    python3 tools/clone_validate.py <appid> <dir with the game's files> [--styles ps,xbox] [--keep]

<dir> must be laid out like the game's install folder and hold every file the profile touches
(copy them from the Deck with .claude/scripts/deck.sh --from). The tool

  1. builds a fake Steam library in a temp folder and clones the files into it (APFS
     copy-on-write `cp -c`: gigabytes cost seconds and no space; plain copy elsewhere),
  2. hashes every file (SHA-256),
  3. runs   style1 -> style2 -> style1 -> original   through the real Engine, timing each step,
  4. checks that the files are byte-identical to the originals after "original", that file
     sizes do not grow when styles are switched, and that the engine reports no error.

Exit status 0 = all good. Use --keep to leave the patched clone for a visual check.
Config-value games (10 Second Ninja X): <dir> holds the pinned build's data file(s) at the root
(`data.win`) and the real save at its path relative to the Proton prefix
(`drive_c/users/steamuser/AppData/.../10SNX.sav`). The save is placed in the fake library's
`compatdata/<appid>/pfx/` and the cycle must give back the pristine save byte for byte (valid when
the game changed nothing else, which a copy made while the game is closed guarantees).
Giant archives (Quantum Break, Saint Seiya) are not cloned this way: validate those read-only on
the Deck (profile.check + in-memory render), see skill `testing-glyph-profiles`.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))

from button_glyphs import engine  # noqa: E402
from button_glyphs.profiles import PROFILES  # noqa: E402

GLYPHS = ROOT / "py_modules" / "button_glyphs" / "glyphs"


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def profile_files(profile):
    if getattr(profile, "files_level", False):
        return [e["file"] for e in profile.files]
    if getattr(profile, "entry_level", False):
        return [t["file"] for t in profile.targets()]
    return [profile.file]


def profile_styles(profile):
    if getattr(profile, "styles", None):
        return [s for s in profile.styles if s in ("ps", "xbox")]
    return list(getattr(profile, "patch_styles", None) or ("ps", "xbox"))


def clone(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        if subprocess.run(["cp", "-c", str(src), str(dst)], capture_output=True).returncode == 0:
            return
    shutil.copyfile(src, dst)


def make_library(home, appid, name, installdir, buildid):
    steamapps = Path(home) / ".local" / "share" / "Steam" / "steamapps"
    (steamapps / "common" / installdir).mkdir(parents=True)
    root = steamapps.parent
    (steamapps / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n' % root)
    (steamapps / ("appmanifest_%s.acf" % appid)).write_text(
        '"AppState"\n{\n\t"appid"\t\t"%s"\n\t"name"\t\t"%s"\n\t"StateFlags"\t\t"4"\n'
        '\t"installdir"\t\t"%s"\n\t"buildid"\t\t"%s"\n}\n' % (appid, name, installdir, buildid))
    return steamapps / "common" / installdir


def validate(appid, src_dir, styles=None, profiles=None, glyph_dir=None, keep=False,
             buildid="0", installdir="Game", log=print):
    """Returns a report dict: {ok, problems, steps, files, kept}."""
    profiles = profiles or PROFILES
    if appid not in profiles:
        return {"ok": False, "problems": ["appid %s has no registered profile" % appid], "steps": []}
    profile = profiles[appid]
    src_dir = Path(src_dir)
    config = getattr(profile, "config_level", False)
    if config:
        if buildid == "0" and len(profile.builds) == 1:
            buildid = next(iter(profile.builds))
        if buildid not in profile.builds:
            return {"ok": False, "steps": [],
                    "problems": ["build %s is not pinned by the profile (use --buildid)" % buildid]}
        files = [profile.builds[buildid]["file"], profile.file]
    else:
        files = profile_files(profile)
    missing = [f for f in files if not (src_dir / f).is_file()]
    if missing:
        return {"ok": False, "steps": [],
                "problems": ["missing in %s: %s" % (src_dir, ", ".join(missing))]}

    styles = styles or profile_styles(profile)
    if not styles:
        return {"ok": False, "steps": [], "problems": ["profile has no patch style"]}
    cycle = [styles[0]] + styles[1:] + [styles[0], "original"]

    tmp = Path(tempfile.mkdtemp(prefix="bg-clone-"))
    report = {"ok": False, "problems": [], "steps": [], "files": files, "kept": None}
    try:
        game = make_library(tmp, appid, profile.name, installdir, buildid)
        paths = {f: game / f for f in files}
        if config:      # the save lives in the Proton prefix of the (fake) library, not in the game folder
            prefix = tmp / ".local" / "share" / "Steam" / "steamapps" / "compatdata" / appid / "pfx"
            paths[profile.file] = prefix / profile.file
        for f in files:
            clone(src_dir / f, paths[f])
        before = {f: sha256(paths[f]) for f in files}
        size0 = {f: os.path.getsize(paths[f]) for f in files}
        log("cloned %d file(s), %.1f MB" % (len(files), sum(size0.values()) / 1e6))

        eng = engine.Engine(str(tmp), str(tmp / "settings"), str(tmp / "runtime"),
                            str(glyph_dir or GLYPHS), profiles=profiles, proc_root=str(tmp / "no-proc"))
        sizes_after_style = {}
        for style in cycle:
            t = time.time()
            result = eng.set_style(appid, style)
            took = time.time() - t
            status = next((g for g in eng.list_games() if g["appid"] == appid), {})
            total = sum(os.path.getsize(paths[f]) for f in files)
            step = {"style": style, "ok": result["ok"], "changed": result.get("changed"),
                    "seconds": round(took, 1), "mb": round(total / 1e6, 1),
                    "state": status.get("state"), "message": status.get("message", "")}
            report["steps"].append(step)
            log("  %-8s ok=%-5s changed=%-5s %5.1fs  %8.1f MB  %s: %s" % (
                style, step["ok"], step["changed"], took, step["mb"], step["state"], step["message"]))
            if not result["ok"]:
                report["problems"].append("%s failed: %s" % (style, "; ".join(result["errors"])))
            elif status.get("state") != "ok":
                report["problems"].append("%s left the game in state %r" % (style, status.get("state")))
            if style != "original":
                snapshot = {f: os.path.getsize(paths[f]) for f in files}
                if style in sizes_after_style and sizes_after_style[style] != snapshot:
                    report["problems"].append("files grew when %s was applied again" % style)
                sizes_after_style.setdefault(style, snapshot)

        after = {f: sha256(paths[f]) for f in files}
        for f in files:
            if after[f] != before[f]:
                report["problems"].append("NOT identical after 'original': %s" % f)
        log("undo: %d/%d file(s) byte-identical to the originals" % (
            sum(after[f] == before[f] for f in files), len(files)))
        if keep:
            report["kept"] = str(tmp)
        report["ok"] = not report["problems"]
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("appid")
    ap.add_argument("src_dir")
    ap.add_argument("--styles", help="comma separated, default: the profile's styles")
    ap.add_argument("--keep", action="store_true", help="keep the patched clone and print its path")
    ap.add_argument("--buildid", default="0")
    ap.add_argument("--installdir", default="Game")
    args = ap.parse_args(argv)
    styles = args.styles.split(",") if args.styles else None
    report = validate(args.appid, args.src_dir, styles, keep=args.keep, buildid=args.buildid,
                      installdir=args.installdir)
    for p in report["problems"]:
        print("PROBLEM:", p, file=sys.stderr)
    print("RESULT:", "PASS" if report["ok"] else "FAIL")
    if report.get("kept"):
        print("clone kept at", report["kept"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
