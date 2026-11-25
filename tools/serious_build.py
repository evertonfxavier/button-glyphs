#!/usr/bin/env python3
"""Dev tool: tables of every button-prompt texture of the three Croteam games (Serious Sam HD FE / TSE, Serious Sam 2).

    python3 tools/serious_build.py build [--appid 41000 ...] [--out FILE] [--report FILE] [--sheet PNG]
    python3 tools/serious_build.py build --dir 41000=<install dir copy> ... --build 41000=5820849 ...
    python3 tools/serious_build.py scan  --appid 41010 --request request.json --out facts.json     (raw facts, for exploring)

`build` reads the game's .gro archives (plain ZIPs, see gro.py) BOUNDED: only central directories plus the few entries it
needs, never a whole archive (the biggest is 1.9 GB). Two sources, one code path (the REMOTE part below is plain stdlib and runs
on either side):

  host mode   (default)  `.claude/scripts/deck.sh --check`, then the REMOTE part is sent on STDIN of `deck.sh 'python3 -'`
              together with the plugin's own steam.py and a JSON request (no game path on the command line: the guard hook scans
              command text). READ-ONLY on the Deck: it opens files for reading and nothing else.
  dir mode    `--dir <appid>=<install dir>`: a local copy of the install folder (the .gro files at their usual relative paths).

What it produces (nothing of the game's pixels, only hashes, ids, sizes):
  * py_modules/button_glyphs/profiles/serious_tables.py   TEXTURES / PAD_SCREEN / WRAPPERS / MOUNT per game, marked generated
  * --report FILE   a text report: unlisted candidates, .rsc references, font and exe hits, catalog suggestions
  * --sheet PNG     a contact sheet of the decoded textures in game orientation (Pillow; scratch only, never committed)

The table pins each ORIGINAL entry by SHA-256 and CRC-32 of the uncompressed bytes (a profile refuses any other build) and by
its place in the load order: the winner of a name is the LAST archive that has it (alphabetical order of the file names, checked
against the game's own log). The strategy is an overlay built at apply time with croteam_tex.reencode(original bytes, new pixels).
"""
import argparse
import base64
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))

# ===== REMOTE BEGIN (self-contained, stdlib only; this text is what runs on the Deck) =====
import base64
import hashlib
import json
import os
import re
import sys
import time
import types
import zipfile
import zlib

SCHEMA = 1
MAX_ENTRY = 8 << 20           # an entry bigger than this is listed, never read (fetch / grep)
GREP_MAX_ENTRY = 16 << 20
GREP_BUDGET = 256 << 20       # inflated bytes read by all greps together
BIN_CAP = 64 << 20            # one executable / dll
BIN_CHUNK = 4 << 20
MAX_HITS = 40
TIME_BUDGET = 240.0          # seconds for the greps together; what was not reached is reported


def _lc(name):
    return name.replace("\\", "/").lower()


def _read_text(path, cap=1 << 20):
    try:
        with open(path, "rb") as f:
            return f.read(cap).decode("utf-8", "replace")
    except OSError:
        return ""


def _log_order(text):
    """The .gro files the game says it mounted, in order (the LAST 'Loading group files' block)."""
    blocks, cur = [], None
    for line in text.splitlines():
        if re.search(r"Loading group files|Mounting|group files", line, re.I) and "gro:" not in line:
            cur = []
            blocks.append(cur)
            continue
        m = re.search(r"([\w./\-]+\.gro): (\d+) files", line)
        if m:
            if cur is None:
                cur = []
                blocks.append(cur)
            cur.append([m.group(1), int(m.group(2))])
    blocks = [b for b in blocks if b]
    return blocks[-1] if blocks else []


def remote_scan(install, req, manifest=None):
    """Facts about one install folder. Reads central directories, and the entries named in the request."""
    out = {"schema": SCHEMA, "ok": True, "appid": str(req.get("appid", "")), "manifest": manifest or {},
           "install_name": os.path.basename(install.rstrip("/")), "problems": []}
    mount = os.path.join(install, req.get("mount", "."))
    if not os.path.isdir(mount):
        return {"schema": SCHEMA, "ok": False, "error": "mount folder %r is missing" % req.get("mount")}
    names = sorted(n for n in os.listdir(mount) if n.lower().endswith(".gro") and os.path.isfile(os.path.join(mount, n)))
    skip = [re.compile(p, re.I) for p in req.get("skip_gros", [])]
    gros, zips = [], []
    for n in names:
        path = os.path.join(mount, n)
        try:
            z = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            out["problems"].append("%s: %s" % (n, exc))
            continue
        zips.append(z)
        gros.append({"name": n, "rel": os.path.relpath(path, install).replace(os.sep, "/"), "size": os.path.getsize(path),
                     "entries": len(z.infolist()), "skipped_for_names": any(r.search(n) for r in skip)})
    out["gros"] = gros
    log = req.get("log")
    log_gros = _log_order(_read_text(os.path.join(install, log))) if log else []
    out["log"] = log
    out["log_gros"] = log_gros
    if log_gros:
        mounted = [os.path.basename(g[0]) for g in log_gros]
        out["log_matches_sorted_order"] = mounted == [g["name"] for g in gros if os.path.basename(g["name"]) in mounted]
        out["log_entry_counts_match"] = all(
            any(g["name"] == os.path.basename(l[0]) and g["entries"] == l[1] for g in gros) for l in log_gros)

    # ---- listing: every layer that has a selected name (central directory only) ----
    sel = [re.compile(p, re.I) for p in req.get("select", [])]
    fetch = {_lc(n) for n in req.get("fetch", [])}
    listing = {}
    infos = {}
    last = {}
    for gi, z in enumerate(zips):
        for info in z.infolist():
            low = _lc(info.filename)
            last[low] = gi
            if low in fetch or any(r.search(info.filename) for r in sel):
                listing.setdefault(low, []).append([gi, info.filename, info.file_size, info.compress_size,
                                                    info.compress_type, info.CRC, info.flag_bits & 1])
                infos[(low, gi)] = info
    out["listing"] = listing

    # ---- fetch: the winning copy of each requested name, CRC checked by zipfile ----
    fetched, errors = {}, {}
    for low in sorted(fetch):
        layers = listing.get(low)
        if not layers:
            errors[low] = "absent"
            continue
        gi, real, usize = layers[-1][0], layers[-1][1], layers[-1][2]
        if layers[-1][6]:
            errors[low] = "encrypted"
        elif usize > req.get("max_entry", MAX_ENTRY):
            errors[low] = "too big (%d)" % usize
        else:
            try:
                data = zips[gi].read(infos[(low, gi)])
                fetched[low] = {"gro": gi, "name": real, "z": base64.b64encode(zlib.compress(data, 6)).decode("ascii")}
            except Exception as exc:  # noqa: BLE001 - reported, never hidden
                errors[low] = "%s: %s" % (type(exc).__name__, exc)
    out["fetched"], out["fetch_errors"] = fetched, errors

    # ---- grep: byte patterns over the inflated bytes of matching entries (text-ish resources), several jobs ----
    hits = []
    out["grep_skipped"] = []
    budget = req.get("grep_budget", GREP_BUDGET)
    deadline = time.time() + req.get("time_budget", TIME_BUDGET)
    for job in req.get("grep", []):
        name_rx = re.compile(job["names"], re.I)
        pats = [(label, re.compile(p.encode("latin-1"), re.I)) for label, p in sorted(job["patterns"].items())]
        cap = job.get("max_entry", GREP_MAX_ENTRY)
        for gi, z in enumerate(zips):
            for info in z.infolist():
                if not name_rx.search(info.filename):
                    continue
                if time.time() > deadline or info.file_size > cap or info.flag_bits & 1 or budget < info.file_size:
                    out["grep_skipped"].append([job["label"], gi, info.filename, info.file_size])
                    continue
                budget -= info.file_size
                try:
                    data = z.read(info)
                except Exception as exc:  # noqa: BLE001
                    out["problems"].append("%s %s: %s" % (gros[gi]["name"], info.filename, exc))
                    continue
                found = {}
                for label, rx in pats:
                    m = sorted({x.group(0).decode("latin-1") for x in rx.finditer(data)})
                    if m:
                        found[label] = m[:MAX_HITS]
                if found:
                    hits.append({"job": job["label"], "gro": gi, "name": info.filename, "size": info.file_size, "hits": found,
                                 "active": last.get(_lc(info.filename)) == gi})
    out["grep_budget_left"] = budget
    out["grep_hits"] = hits

    # ---- bin: byte patterns in executables, narrow and UTF-16 (two alignments) ----
    b = req.get("bin")
    bin_hits = []
    if b:
        rxs = [(label, re.compile(p.encode("latin-1"), re.I)) for label, p in sorted(b["patterns"].items())]
        for rel in b["files"]:
            path = os.path.join(install, rel)
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            if size > BIN_CAP:
                bin_hits.append({"file": rel, "error": "too big (%d)" % size})
                continue
            with open(path, "rb") as f:
                data = f.read(BIN_CAP)
            found = {}
            for label, rx in rxs:
                got = set()
                for view, tag in ((data, "a"), (data[0::2], "w0"), (data[1::2], "w1")):
                    for x in rx.finditer(view):
                        got.add("%s:%s" % (tag, x.group(0).decode("latin-1")))
                if got:
                    found[label] = sorted(got)[:MAX_HITS]
            bin_hits.append({"file": rel, "size": size, "hits": found})
    out["bin_hits"] = bin_hits
    for z in zips:
        z.close()
    return out


def remote_main(steam_source, request_json):
    """Entry point of the program sent to the Deck: prints one JSON object (errors included)."""
    try:
        req = json.loads(request_json)
        steam = types.ModuleType("steam")
        exec(compile(steam_source, "steam.py", "exec"), steam.__dict__)
        home = os.path.expanduser("~")
        app = steam.installed_apps(home).get(str(req["appid"]))
        if not app or not os.path.isdir(app["install_path"]):
            result = {"schema": SCHEMA, "ok": False, "error": "game %s is not installed" % req["appid"]}
        else:
            manifest = {}
            text = _read_text(app["manifest"], 1 << 16)
            for k in ("name", "installdir", "buildid", "StateFlags", "TargetBuildID", "SizeOnDisk"):
                m = re.search(r'"%s"\s+"([^"]*)"' % re.escape(k), text)
                manifest[k] = m.group(1) if m else ""
            result = remote_scan(app["install_path"], req, manifest)
    except Exception as exc:  # noqa: BLE001
        result = {"schema": SCHEMA, "ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    print(json.dumps(result, sort_keys=True))
# ===== REMOTE END =====

STEAM_PY = ROOT / "py_modules" / "button_glyphs" / "steam.py"
DECK_SH = ROOT.parent / ".claude" / "scripts" / "deck.sh"
OUT_PY = ROOT / "py_modules" / "button_glyphs" / "profiles" / "serious_tables.py"

from button_glyphs import croteam_tex as T  # noqa: E402

TEXTURE_LISTING = r"(/Textures/(Menu|Interface)/.*|/[^/]*(xbox|gamepad|controller|dpad|thumbstick|axesbutton|_button)[^/]*)\.(tex|rsc)$"
CANDIDATE_RX = re.compile(r"(xbox|button|btn|gamepad|controller|axes|dpad|trigger|shoulder|thumbstick|arrow|"
                          r"keyboard|mouse|prompt|htp|hint|_pad|pad_)", re.I)
# names that look like candidates and were looked at (or are plainly not controller art): (regex on the file name, why)
NOT_TOUCHED_RX = [
    (r"^Htp_", "'how to play' pictures: 7 of 10 decoded (DXT1 screenshots, no pad glyph); Difficulty/Netricsa/Score are 512x256 raw (format 4) and were not decoded"),
    (r"ScrollBar(Top|Bottom)?Arrow|^select_button_|^button_outbar|^Upper_background_bar|^Bottom_background_bar|background", "menu chrome (scroll bar, selection bars, backgrounds): no controller art"),
    (r"^BUY_Button|^xbox_|^XboxLive|^Sam_Menu_XBox|Background_Blue_Xbox", "Xbox LIVE / buy / menu pictures named after the console: not a pad prompt"),
    (r"^MousePointer|^MenuBackgroundOverlay", "mouse pointer / menu overlay"),
    (r"^Crosshair\d+|Crossbow_Arrows|^Objective|^WeaponBarButton|^ScannerArrow|^WeaponArrow", "crosshairs, HUD bars, objective/scanner/weapon HUD pieces: no controller art"),
    (r"^Controller\.tex$", "a 3D level prop (SS2 08_Sirius), not the pad"),
    (r"^CMSXbox", "Xbox LIVE menu layouts (no pad prompt)"),
    (r"^CMSControllerOptions\.rsc$", "NOT a texture: the SS2 controller-layout screen, in the table as kind layout (gadget rectangles incl. the four arrow labels)"),
    (r"^AxesButton_Rth", "32x32, only in Patch_02_100, layout croteam_tex does not parse; name suggests a right-thumb stick picture: UNKNOWN, not seen"),
]
REFS_PATTERN = (r"Content/[A-Za-z0-9_/\-\. ]{1,160}?(XBox|Gamepad|Controller|AxesButton|Dpad|_button|Thumbstick|"
                r"Shoulder|Trigger|Cross)[A-Za-z0-9_\-]{0,40}\.(tex|rsc)")


# --------------------------------------------------------------------------- what we look for (hand-classified)
# (path below the content root, role, kind, action, catalog role, note). Roles are the plugin's glyph vocabulary
# (skill glyph-mapping-ps-xbox); a game's own roles are named in the note. kind/action:
#   button   a glyph the game draws for one pad control                      -> replace (draw the PS look)
#   stick    stick pictures (same shape on PS)                               -> optional
#   dpad     d-pad pictures                                                  -> optional
#   pad_picture  the whole-controller drawing of the layout screen           -> pad_art (the user's PS drawing)
#   pad_overlay  grey arrow pairs drawn around the pad picture                -> keep
#   pad_arrow    yellow arrows of the layout screen                           -> keep, re-point
HD_MENU = "Content/SeriousSamHD/Textures/Menu/"
HD_TSE_MENU = "Content/SeriousSamHD_TSE/Textures/Menu/"
HD_ATLAS = "Content/SeriousSamHD/Textures/GlobalAtlas.rsc"
SS2_MENU = "Content/SeriousSam2/Textures/Menu/"


def _rows(base, items):
    return [(base + name + ".tex",) + tuple(rest) for name, *rest in items]


_FACE = [("XBoxButtonA", "a"), ("XBoxButtonB", "b"), ("XBoxButtonX", "x"), ("XBoxButtonY", "y")]
_HD_PAD = [
    ("GamepadPerspective", "controller", "pad_picture", "pad_art", "controller", "Xbox 360 pad drawing, DXT5 512x512"),
    ("GamepadStrafeUD", "pad_overlay", "pad_overlay", "keep", "", "grey up/down arrows, L8A8 (format 6): croteam_tex cannot write it"),
    ("GamepadStrafeLR", "pad_overlay", "pad_overlay", "keep", "", "grey left/right arrows, L8A8 (format 6)"),
    ("GamepadLookUD", "pad_overlay", "pad_overlay", "keep", "", "grey up/down arrows, L8A8 (format 6)"),
    ("GamepadLookLR", "pad_overlay", "pad_overlay", "keep", "", "grey left/right arrows, L8A8 (format 6)"),
    ("ControllerForwardArrow", "pad_arrow_forward", "pad_arrow", "keep", "", "yellow arrow, left stick forward"),
    ("ControllerRightArrow", "pad_arrow_right", "pad_arrow", "keep", "", "yellow arrow, left stick right"),
    ("ControllerLookUpArrow", "pad_arrow_lookup", "pad_arrow", "keep", "", "yellow arrow, right stick up"),
    ("ControllerLookRightArrow", "pad_arrow_lookright", "pad_arrow", "keep", "", "yellow arrow, right stick right (curved)"),
]
_HD_OVAL = [("A_button", "a"), ("B_button", "b"), ("X_button", "x"), ("Y_button", "y")]
_HD_STICKS = [("XBoxThumbstick", "stick", ""), ("XBoxThumbstickU", "stick_up", "_up"), ("XBoxThumbstickD", "stick_down", "_down"),
              ("XBoxThumbstickL", "stick_left", "_left"), ("XBoxThumbstickR", "stick_right", "_right"),
              ("XBoxThumbstickUR", "stick_up_right", ""), ("XBoxThumbstickRD", "stick_right_down", ""),
              ("XBoxThumbstickDL", "stick_down_left", ""), ("XBoxThumbstickLU", "stick_left_up", "")]


def _hd_spec(tse):
    spec = []
    for n, r in _FACE:
        spec.append((HD_MENU + n + ".tex", r, "button", "replace", r, "white disc, dark letter (plain look)"))
    for n, r in _HD_OVAL:
        spec.append((HD_MENU + n + ".tex", r, "button", "replace", r, "glossy coloured oval, 32x32 RGBA (the older HUD/menu hint art)"))
    spec.append((HD_MENU + "AxesButton.tex", "stick", "stick", "optional", "lstick", "grey stick top, 32x32 RGBA; same on PS"))
    if tse:
        spec += [
            (HD_MENU + "XBoxButtonStart.tex", "start", "button", "replace", "start", "START with triangle, white"),
            (HD_MENU + "XBoxButtonBack.tex", "select", "button", "replace", "select", "BACK with triangle, white"),
            (HD_MENU + "XBoxShoulderButtonL.tex", "lb", "button", "replace", "lb", "LB, white rounded shape"),
            (HD_MENU + "XBoxShoulderButtonR.tex", "rb", "button", "replace", "rb", "RB, white rounded shape"),
            (HD_MENU + "XBoxTriggerButtonL.tex", "lt", "button", "replace", "lt", "LT, white trigger shape"),
            (HD_MENU + "XBoxTriggerButtonR.tex", "rt", "button", "replace", "rt", "RT, white trigger shape"),
            (HD_MENU + "XBoxThumbstickTriggerL.tex", "l3", "button", "replace", "l3",
             "L over a pad with a down arrow = stick click (translation key Menu.Gamepad.ThumbstickClick); confirm in game"),
            (HD_MENU + "XBoxThumbstickTriggerR.tex", "r3", "button", "replace", "r3", "R, same as L3; confirm in game"),
        ]
        for n, r, c in _HD_STICKS:
            spec.append((HD_MENU + n + ".tex", r, "stick", "optional", "lstick" + c if c else "",
                         "generic stick picture (the name has no L/R): movement direction"))
        spec += [
            (HD_TSE_MENU + "XBoxButtonBack.tex", "select", "button", "replace", "select", "duplicate of the Content/SeriousSamHD one (same pixels), named by MenuPalette.rsc"),
            (HD_TSE_MENU + "XBoxTriggerButtonR.tex", "rt", "button", "replace", "rt", "duplicate of the Content/SeriousSamHD one (same pixels), named by MenuPalette.rsc"),
        ]
    for n, role, kind, action, crole, note in _HD_PAD:
        spec.append((HD_MENU + n + ".tex", role, kind, action, crole, note))
    return spec


GAMES = {
    "41000": {
        "title": "Serious Sam HD: The First Encounter", "short": "tfe", "build": "5820849",
        "mount": "Content/SeriousSamHD", "log": "Log/SamHD.log", "exe": "Bin/SamHD.exe",
        "spec": _hd_spec(False), "atlas": HD_ATLAS,
        "not_touched": [
            ("Content/SeriousSamHD/Textures/Menu/PC_Enter.tex", "keyboard keycap, not a pad prompt"),
            ("Content/SeriousSamHD/Textures/Menu/PC_Esc.tex", "keyboard keycap"),
            ("Content/SeriousSamHD/Textures/Menu/PC_F1.tex", "keyboard keycap"),
            ("Content/SeriousSamHD/Textures/Menu/PC_F2.tex", "keyboard keycap"),
            ("Content/SeriousSamHD/Textures/Interface/Crosshairs/Xbox_cross.tex", "a crosshair (circle with a dot), no button drawn"),
            ("Content/SeriousSamHD/Textures/Interface/Arrow.tex", "arrow in a box, layers All_117121 / All_582898 only: not a pad glyph, referenced by no prompt table"),
            ("Content/SeriousSamHD/Textures/Interface/DirectionArrow.tex", "HUD direction arrow"),
            ("Content/SeriousSamHD/Textures/Menu/arrow_selected.tex", "menu selection wedge"),
            ("Content/SeriousSamHD/Textures/Menu/arrow_selectedV.tex", "menu selection wedge"),
            ("Content/SeriousSamHD/Textures/Menu/MousePointer.tex", "mouse pointer"),
        ],
    },
    "41010": {
        "title": "Serious Sam HD: The Second Encounter", "short": "tse", "build": "5821931",
        "mount": "Content/SeriousSamHD_TSE", "log": "Log/SamHD_TSE.log", "exe": "Bin/SamHD_TSE.exe",
        "spec": _hd_spec(True), "atlas": HD_ATLAS,
        "not_touched": [
            ("Content/SeriousSamHD/Textures/Menu/PC_Enter.tex", "keyboard keycap"),
            ("Content/SeriousSamHD/Textures/Menu/PC_Esc.tex", "keyboard keycap"),
            ("Content/SeriousSamHD/Textures/Menu/PC_F1.tex", "keyboard keycap"),
            ("Content/SeriousSamHD/Textures/Menu/PC_F2.tex", "keyboard keycap"),
            ("Content/SeriousSamHD/Textures/Menu/KeyboardEnter.tex", "keyboard key"),
            ("Content/SeriousSamHD/Textures/Menu/KeyboardTab.tex", "keyboard key"),
            ("Content/SeriousSamHD/Textures/Menu/KeyboardBackspace.tex", "keyboard key"),
            ("Content/SeriousSamHD/Textures/Menu/KeyboardArrowU.tex", "keyboard key"),
            ("Content/SeriousSamHD/Textures/Menu/KeyboardArrowD.tex", "keyboard key"),
            ("Content/SeriousSamHD/Textures/Menu/KeyboardArrowL.tex", "keyboard key"),
            ("Content/SeriousSamHD/Textures/Menu/KeyboardArrowR.tex", "keyboard key"),
            ("Content/SeriousSamHD/Textures/Menu/MouseButtonLeft.tex", "mouse picture"),
            ("Content/SeriousSamHD/Textures/Menu/MouseButtonMiddle.tex", "mouse picture"),
            ("Content/SeriousSamHD/Textures/Menu/MouseButtonRight.tex", "mouse picture"),
            ("Content/SeriousSamHD/Textures/Interface/Crosshairs/Xbox_cross.tex", "a crosshair, no button drawn"),
            ("Content/SeriousSamHD/Textures/Interface/Arrow.tex", "arrow in a box, not a pad glyph"),
            ("Content/SeriousSamHD/Textures/Interface/DirectionArrow.tex", "HUD direction arrow"),
            ("Content/SeriousSamHD/Textures/Menu/arrow_selected.tex", "menu selection wedge"),
            ("Content/SeriousSamHD/Textures/Menu/arrow_selectedV.tex", "menu selection wedge"),
            ("Content/SeriousSamHD/Textures/Menu/MousePointer.tex", "mouse pointer"),
        ],
    },
    "204340": {
        "title": "Serious Sam 2", "short": "ss2", "build": "24849618",
        "mount": ".", "log": "All.log", "exe": "Bin/Sam2Game.dll",
        "layout": "Content/SeriousSam2/Menus/CMSControllerOptions.rsc",
        "spec": _rows(SS2_MENU, [
            ("A_button", "a", "button", "replace", "a", "glossy green oval, 128x128 raw BGRA (older layers hold 32x32 versions)"),
            ("B_button", "b", "button", "replace", "b", "glossy red oval"),
            ("X_button", "x", "button", "replace", "x", "glossy blue oval"),
            ("Y_button", "y", "button", "replace", "y", "glossy yellow oval"),
            ("Use_button", "black", "button", "replace", "rb",
             "dark/black oval = the original Xbox BLACK button; drawn for the Use action (HudParams ButtonUse, inline in two fonts); PS role is a guess, see the report"),
            ("White_button", "white", "button", "replace", "lb",
             "white oval = the original Xbox WHITE button (HudParams ButtonWhite); PS role is a guess, see the report"),
            ("AxesButton", "stick", "stick", "optional", "lstick", "dark stick top; same on PS"),
            ("Dpad_button_up", "dpad_up", "dpad", "optional", "dpad_up", "round d-pad, up highlighted"),
            ("Dpad_button_down", "dpad_down", "dpad", "optional", "dpad_down", "round d-pad, down highlighted"),
            ("Dpad_button_left", "dpad_left", "dpad", "optional", "dpad_left", "round d-pad, left highlighted"),
            ("Dpad_button_right", "dpad_right", "dpad", "optional", "dpad_right", "round d-pad, right highlighted"),
            ("controller", "controller", "pad_picture", "pad_art", "controller", "original Xbox 'Duke S' pad, A/B/X/Y baked in, 1024x1024 raw BGRA"),
            ("ControllerForwardArrow", "pad_arrow_forward", "pad_arrow", "keep", "", "yellow arrow, left stick forward"),
            ("ControllerRightArrow", "pad_arrow_right", "pad_arrow", "keep", "", "yellow arrow, left stick right"),
            ("ControllerLookUpArrow", "pad_arrow_lookup", "pad_arrow", "keep", "", "yellow arrow, right stick up"),
            ("ControllerLookRightArrow", "pad_arrow_lookright", "pad_arrow", "keep", "", "yellow arrow, right stick right (curved)"),
        ]),
        "atlas": None,
        "not_touched": [
            ("Content/SeriousSam2/Textures/Menu/PC_Enter.tex", "keyboard keycap, 64x64"),
            ("Content/SeriousSam2/Textures/Menu/PC_Esc.tex", "keyboard keycap"),
            ("Content/SeriousSam2/Textures/Menu/PC_Tab.tex", "keyboard keycap"),
            ("Content/SeriousSam2/Textures/Menu/AxesButton_glow.tex", "glow behind the stick; older 64x64 layout croteam_tex does not parse"),
            ("Content/SeriousSam2/Textures/Menu/Controller_arrowHead.tex", "16x16 arrow head; older layout croteam_tex does not parse; pixels are not a prompt"),
            ("Content/SeriousSam2/Textures/Interface/Crosshairs/XBox_cross.tex", "a gamepad crosshair, no button drawn (only layer All_PC_01)"),
            ("Content/SeriousSam2/Textures/Menu/arrow_selected.tex", "menu selection wedge"),
            ("Content/SeriousSam2/Textures/Menu/arrow_selectedV.tex", "wide selection wedge"),
        ],
    },
}

# --------------------------------------------------------------------------- requests, facts


def make_request(game, appid):
    fetch = [r[0] for r in game["spec"]]
    fetch += [p[:-4] + ".rsc" for p in fetch if p.endswith(".tex")]      # wrappers (absent ones are reported, harmless)
    if game.get("atlas"):
        fetch.append(game["atlas"])
    if game.get("layout"):
        fetch.append(game["layout"])
    exe = game["exe"]
    bin_patterns = {
        "hd": {"xbox": r"XBox[A-Za-z0-9]{0,30}", "pad": r"GAMEPAD_[A-Z]{1,20}", "arrows": r"Controller[A-Za-z]{0,20}Arrow",
               "screen": r"Gamepad(Strafe|Look|Perspective)[A-Za-z]{0,10}", "stick": r"Thumbstick[A-Za-z]{0,20}", "img": r"<img src=.[A-Za-z0-9_]{1,40}.>"},
        "ss2": {"nhep": r"nhep_ptxButton[A-Za-z_]{0,30}", "lbl": r"lbl[A-Za-z]{0,20}(Button|Thumb|Trigger|Arrow)[A-Za-z]{0,20}",
                "dpad": r"Dpad_button[A-Za-z_]*", "use": r"(Use|White)_button", "img": r"<img src=.[A-Za-z0-9_]{1,40}.>"},
    }["ss2" if game["short"] == "ss2" else "hd"]
    return {
        "appid": appid, "mount": game["mount"], "log": game["log"], "max_entry": 16 << 20,
        "select": [TEXTURE_LISTING], "fetch": fetch,
        "grep": [
            {"label": "refs", "names": r"\.(rsc|fnt|tbl|cfg|xml|txt)$", "patterns": {"tex": REFS_PATTERN, "atlas": r"[A-Za-z_]{0,20}atlas[A-Za-z_]{0,20}"}},
            {"label": "fonts", "names": r"\.fnt$", "patterns": {"tex": r"Content/[A-Za-z0-9_/\-\. ]{1,160}\.tex"}},
        ],
        "bin": {"files": [exe], "patterns": bin_patterns},
    }


def _ns_remote():
    ns = {"__name__": "serious_remote"}
    exec(compile(remote_source(), "remote", "exec"), ns)
    return ns


def remote_source():
    text = Path(__file__).read_text(encoding="utf-8")
    start = text.index("# ===== REMOTE BEGIN")
    start = text.index("\n", start) + 1
    return text[start:text.index("# ===== REMOTE END")]


def build_script(request, steam_source=None, remote=None):
    steam_source = steam_source if steam_source is not None else STEAM_PY.read_text(encoding="utf-8")
    remote = remote if remote is not None else remote_source()
    return "%s\nremote_main(%r, %r)\n" % (remote, steam_source, json.dumps(request, sort_keys=True))


def run_remote(script, deck_sh=None, timeout=900):
    """`script` on stdin of `deck.sh 'python3 -'` (the command text names no game path)."""
    deck_sh = str(deck_sh or os.environ.get("DECK_SH") or DECK_SH)
    subprocess.run([deck_sh, "true"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    return subprocess.run([deck_sh, "python3 -"], input=script, capture_output=True, text=True, timeout=timeout)


def check_deck(deck_sh=None):
    deck_sh = str(deck_sh or os.environ.get("DECK_SH") or DECK_SH)
    p = subprocess.run([deck_sh, "--check"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def parse_result(stdout):
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("the Deck answered without a JSON object")


def collect(request, dir_path=None, build=None):
    """Facts for one request: from a local install folder, or read-only from the Deck."""
    if dir_path:
        return _ns_remote()["remote_scan"](os.path.abspath(dir_path), request, {"buildid": build or ""})
    ok, msg = check_deck()
    if not ok:
        sys.exit("the Deck is not reachable (deck.sh --check): %s" % msg)
    p = run_remote(build_script(request))
    facts = parse_result(p.stdout)
    if not facts.get("ok"):
        sys.exit("the Deck side failed: %s %s" % (facts.get("error"), p.stderr.strip()[:300]))
    return facts


def fetched_bytes(facts, name):
    f = facts["fetched"].get(name.lower())
    return zlib.decompress(base64.b64decode(f["z"])) if f else None


# --------------------------------------------------------------------------- analysis


class TableError(Exception):
    pass


def _hex_crc(v):
    return "%08x" % (v & 0xFFFFFFFF)


def _tex_facts(data):
    """(width, height, fmt name, fmt code, mips, writable, kind) of a .tex, or None when croteam_tex refuses it."""
    try:
        t = T.parse(data)
    except T.TexError:
        return None
    if t.tobytes() != data:
        raise TableError("croteam_tex.parse(...).tobytes() is not the identity for a texture")
    if t.kind == "hd":
        mips = len(T.levels(t.width, t.height))
    else:
        mips = struct.unpack_from("<iiIIII", t.head, len(t.head) - 4 - 24)[5]
    return {"width": t.width, "height": t.height, "fmt": t.fmt_name, "fmt_code": t.fmt, "mips": mips,
            "writable": bool(t.supported), "engine": t.kind}


def atlas_info(data):
    """The pixel blocks of a CStaticAtlas .rsc and the parts (texture name + UV rectangle) of each, in pixels."""
    tex = []
    pos = data.find(b"STAR")
    while pos >= 0:
        hit = None
        if pos >= 16 and pos + 8 <= len(data):
            size = struct.unpack_from("<I", data, pos + 4)[0]
            w, h, _flags, fmt = struct.unpack_from("<IIII", data, pos - 16)
            name = T.HD_FORMATS.get(fmt)
            if name and 1 <= w <= 16384 and 1 <= h <= 16384 and pos + 8 + size <= len(data) and T.surface_size(w, h, name) == size:
                hit = {"width": w, "height": h, "fmt": name, "fmt_code": fmt, "mips": len(T.levels(w, h)), "offset": pos, "bytes": size}
        if hit:
            tex.append(hit)
            pos = data.find(b"STAR", pos + 8 + hit["bytes"])
        else:
            pos = data.find(b"STAR", pos + 1)
    parts, group, prev = [], -1, None
    for m in re.finditer(rb"([\x01-\xff])\x00\x00\x00(Content/[\w/\-\. ]+\.tex)", data):
        if m.group(1)[0] != len(m.group(2)):
            continue
        fw, fh, fx, fy = struct.unpack_from("<ffff", data, m.end())
        if prev is None or (fx == 0.0 and fy == 0.0):
            group += 1
        prev = (fx, fy)
        parts.append({"path": m.group(2).decode("ascii"), "group": group, "uv": (fw, fh, fx, fy)})
    for p in parts:
        t = tex[p["group"]] if p["group"] < len(tex) else None
        p["rect"] = None
        if t:
            fw, fh, fx, fy = p.pop("uv")
            vals = (fx * t["width"], fy * t["height"], fw * t["width"], fh * t["height"])
            if all(abs(v - round(v)) < 1e-3 for v in vals):
                p["rect"] = [int(round(v)) for v in vals]
        else:
            p.pop("uv")
    return tex, parts


def layout_rects(data):
    """Gadget rectangles of a Serious Sam 2 menu screen .rsc (CMenuScreenLayout): [(name, class, id, offset, [x, y, w, h])].

    The ident table names the gadgets in order; each gadget's record holds two int32 (class, serial id) and then x, y, w, h as
    four float32 (menu pixels) at a byte offset that is not aligned. Records are found by that shape and by increasing ids;
    the count must equal the number of names, otherwise None (never a guess)."""
    end = data.find(b"EXTY")
    names = [m.group(0).decode("ascii") for m in re.finditer(rb"(?:img|bbr|lbl|opsl|lnk|arr)[A-Za-z0-9]+", data[:end])] if end > 0 else []
    start = data.find(b"OBJS")
    if not names or start < 0:
        return None
    recs, o = [], start
    while o < len(data) - 24:
        klass, serial = struct.unpack_from("<ii", data, o)
        if 1 <= klass <= 64 and 1 <= serial <= 64 and (not recs or serial > recs[-1][1]):
            x, y, w, h = struct.unpack_from("<ffff", data, o + 8)
            if all(v == v and -2000 < v < 4000 and (v == 0 or abs(v) >= 0.01) and abs(v * 8 - round(v * 8)) < 1e-4
                   for v in (x, y, w, h)) and w >= 1 and h >= 0:
                recs.append((klass, serial, o + 8, [x, y, w, h]))
                o += 24
                continue
        o += 1
    if len(recs) != len(names):
        return None
    return [(n, r[0], r[1], r[2], r[3]) for n, r in zip(names, recs)]


def analyse(game, facts, appid):
    """Rows for one game from its facts. Raises TableError on anything that makes the table unsafe."""
    errors, notes = [], []
    gros = facts["gros"]
    names = [g["name"] for g in gros]
    if facts.get("log_gros") and not facts.get("log_matches_sorted_order"):
        errors.append("the game's own log lists the archives in another order than the sorted file names: the winning layer is ambiguous")
    build = facts.get("manifest", {}).get("buildid", "")
    if build and build != game["build"]:
        errors.append("build %s on this machine, the table was asked for %s" % (build, game["build"]))
    listing = facts["listing"]
    rows, seen = [], set()
    atlas_parts = {}
    atlas_row = None
    if game.get("atlas"):
        ad = fetched_bytes(facts, game["atlas"])
        if ad is None:
            errors.append("atlas %s not fetched: %s" % (game["atlas"], facts["fetch_errors"].get(game["atlas"].lower())))
        else:
            tex, parts = atlas_info(ad)
            atlas_parts = dict((p["path"].lower(), p) for p in parts)
            first = T.parse(ad)
            L = listing[game["atlas"].lower()][-1]
            atlas_row = {
                "id": game["atlas"][len("Content/"):-4], "path": L[1], "role": "atlas", "kind": "atlas", "action": "replace_part",
                "catalog_role": "", "note": "CStaticAtlas holding baked copies of small textures; croteam_tex.parse() takes the first pixel block",
                "gro": names[L[0]], "layers": [names[x[0]] for x in listing[game["atlas"].lower()]],
                "width": first.width, "height": first.height, "fmt": first.fmt_name, "fmt_code": first.fmt,
                "mips": len(T.levels(first.width, first.height)), "writable": bool(first.supported),
                "size": len(ad), "csize": L[3], "method": L[4], "sha256": hashlib.sha256(ad).hexdigest(), "crc32": _hex_crc(zlib.crc32(ad)),
                "atlas_textures": [(t["width"], t["height"], t["fmt"], t["mips"], t["offset"], t["bytes"]) for t in tex],
                "atlas_parts": [],
            }
            if (tex[0]["width"], tex[0]["height"]) != (first.width, first.height):
                errors.append("croteam_tex.parse() does not pick the first atlas texture")
    for path, role, kind, action, crole, note in game["spec"]:
        low = path.lower()
        L = listing.get(low)
        data = fetched_bytes(facts, path)
        if not L or data is None:
            errors.append("%s: %s" % (path, facts["fetch_errors"].get(low, "not listed")))
            continue
        if low in seen:
            errors.append("%s listed twice in the spec" % path)
        seen.add(low)
        win = L[-1]
        if zlib.crc32(data) & 0xFFFFFFFF != win[5] & 0xFFFFFFFF or len(data) != win[2]:
            errors.append("%s: fetched bytes do not match the central directory" % path)
        tf = _tex_facts(data) or {"width": None, "height": None, "fmt": "unparsed", "fmt_code": None, "mips": None,
                                  "writable": False, "engine": None}
        row = {
            "id": path[len("Content/"):-4], "path": win[1], "role": role, "kind": kind, "action": action,
            "catalog_role": crole, "note": note, "gro": names[win[0]], "layers": [names[x[0]] for x in L],
            "width": tf["width"], "height": tf["height"], "fmt": tf["fmt"], "fmt_code": tf["fmt_code"], "mips": tf["mips"],
            "writable": tf["writable"], "size": len(data), "csize": win[3], "method": win[4],
            "sha256": hashlib.sha256(data).hexdigest(), "crc32": _hex_crc(zlib.crc32(data)),
        }
        wrapper = listing.get(path[:-4].lower() + ".rsc")
        if wrapper:
            wd = fetched_bytes(facts, path[:-4] + ".rsc")
            row["wrapper"] = {"gro": names[wrapper[-1][0]], "size": wrapper[-1][2], "sha256": hashlib.sha256(wd).hexdigest() if wd else None}
        part = atlas_parts.get(low)
        if part:
            if part["rect"] is None or [part["rect"][2], part["rect"][3]] != [tf["width"], tf["height"]]:
                errors.append("%s: atlas part %s does not have the texture's own size %sx%s" % (path, part["rect"], tf["width"], tf["height"]))
            row["atlas_part"] = {"group": part["group"], "rect": part["rect"]}
            atlas_row["atlas_parts"].append((row["id"], role, part["group"], part["rect"]))
        # refs: active (winning-layer) resources that name this texture
        refs = []
        for h in facts["grep_hits"]:
            if h["job"] != "refs" or not h.get("active", True):
                continue
            for m in h["hits"].get("tex", []):
                if m.lower()[:-4] == path.lower()[:-4]:
                    refs.append("%s:%s" % (names[h["gro"]], h["name"]))
        row["referenced_by"] = sorted(set(refs))
        rows.append(row)
    if atlas_row:
        atlas_row["atlas_parts"] = sorted(atlas_row["atlas_parts"])
        rows.append(atlas_row)
    if game.get("layout"):
        path = game["layout"]
        L = listing.get(path.lower())
        data = fetched_bytes(facts, path)
        if not L or data is None:
            errors.append("layout %s: %s" % (path, facts["fetch_errors"].get(path.lower(), "not listed")))
        else:
            lay = layout_rects(data)
            if lay is None:
                errors.append("layout %s: the gadget records do not match the names, refusing to guess" % path)
            else:
                rows.append({
                    "id": path[len("Content/"):-4], "path": L[-1][1], "role": "pad_layout", "kind": "layout", "action": "repoint_arrows",
                    "catalog_role": "", "note": "controller-layout screen: gadget rectangles (x, y, w, h float32 at the byte offset given); "
                    "the four lbl*Arrow* gadgets place the arrows, imgController the pad picture",
                    "gro": names[L[-1][0]], "layers": [names[x[0]] for x in L], "width": None, "height": None, "fmt": "rsc", "fmt_code": None,
                    "mips": None, "writable": True, "size": len(data), "csize": L[-1][3], "method": L[-1][4],
                    "sha256": hashlib.sha256(data).hexdigest(), "crc32": _hex_crc(zlib.crc32(data)),
                    "layout": [(n, k, i, off, [float(v) for v in r]) for n, k, i, off, r in lay],
                })
    if errors:
        raise TableError("\n".join(errors))
    rows.sort(key=lambda r: (r["kind"] in ("atlas", "layout"), r["id"]))
    return rows, notes


def unlisted_candidates(game, facts):
    """Textures of the Menu / Interface folders whose names look like prompts and that are neither in the table nor excused."""
    known = {r[0].lower() for r in game["spec"]} | {p[:-4].lower() + ".rsc" for p, *_ in game["spec"]}
    known |= {p.lower() for p, _ in game["not_touched"]} | {p[:-4].lower() + ".rsc" for p, _ in game["not_touched"]}
    out = []
    for low, layers in sorted(facts["listing"].items()):
        base = low.rsplit("/", 1)[-1]
        if low in known or not CANDIDATE_RX.search(base):
            continue
        if any(re.search(rx, layers[-1][1].rsplit("/", 1)[-1], re.I) for rx, _ in NOT_TOUCHED_RX):
            continue
        out.append((layers[-1][1], [facts["gros"][x[0]]["name"] for x in layers], layers[-1][2]))
    return out


# --------------------------------------------------------------------------- catalog


def catalog_top(rows):
    """{(catalog_role, WxH): (variant id, verdict)} from `build_button_catalog.py find` (read only)."""
    sys.path.insert(0, str(ROOT / "tools"))
    import build_button_catalog as BC
    cat = json.loads((ROOT / "catalog" / "catalog.json").read_text(encoding="utf-8"))
    top, cands = {}, {}
    for r in rows:
        if r["action"] not in ("replace", "optional", "pad_art") or not r["catalog_role"] or not r["width"]:
            continue
        key = (r["catalog_role"], "%dx%d" % (r["width"], r["height"]))
        if key in top:
            continue
        hits = BC.rank(cat, r["catalog_role"], (r["width"], r["height"]), style="ps")
        if hits:
            top[key] = (hits[0][0]["id"], hits[0][1]["hint"])
            cands[key] = [(v["id"], v["kind"], info["hint"]) for v, info in hits[:4]]
        else:
            top[key] = ("", "nothing in the catalog for this role")
            cands[key] = []
    return top, cands


# --------------------------------------------------------------------------- the generated module

ROW_KEYS = ("id", "path", "role", "kind", "action", "catalog_role", "gro", "layers", "width", "height", "fmt", "fmt_code", "mips",
            "writable", "size", "csize", "method", "sha256", "crc32")
OPTIONAL_KEYS = ("wrapper", "atlas_part", "atlas_textures", "atlas_parts", "layout", "referenced_by", "note")

HEADER = '''"""Generated by tools/serious_build.py from the original game files - do not edit.

Serious Sam HD: The First / The Second Encounter (Steam 41000, 41010) and Serious Sam 2 (204340): every button-prompt
texture, the controller-layout screen textures and the atlas that holds baked copies of some of them, with the SHA-256 and
CRC-32 of the ORIGINAL uncompressed .tex entry, its size and the .gro archive that WINS it (the last archive in load order
that has the name). Nothing here is game art, only ids, sizes and hashes. A profile refuses a build whose entries differ.

Per game:  title, build, mount (folder of the .gro files, relative to the install), load_order [(file, entries, bytes)],
           textures [row], not_touched [(path, why)], not_touched_patterns [(regex on the file name, why)].
           Row keys: %s.
           Optional: %s.
role is the plugin's glyph vocabulary (a b x y lb rb lt rt l3 r3 start select ... see skill glyph-mapping-ps-xbox) or a
game's own name (stick*, pad_arrow_*, black, white); action: replace | optional | pad_art | keep | replace_part | repoint_arrows;
kind layout (Serious Sam 2 only): the controller-layout screen .rsc with every gadget rectangle [x, y, w, h] (float32 at `offset`);
fmt: dxt5 dxt3 rgba8 (HD, R,G,B,A) bgra8 (Serious Engine 2, B,G,R,A) bits16 (HD L8A8) unparsed; writable = croteam_tex can
rewrite it; layers = every archive that has the name, in load order (the last wins).
CATALOG: {"role WxH": (variant id, verdict)} = the first `build_button_catalog.py find --style ps` result when this was generated.
"""
''' % (", ".join(ROW_KEYS), ", ".join(OPTIONAL_KEYS))


def render_module(tables, catalog):
    lines = [HEADER, "", "GAMES = {"]
    for appid in sorted(tables):
        t = tables[appid]
        lines.append("    %r: {" % appid)
        for k in ("title", "build", "mount"):
            lines.append("        %r: %r," % (k, t[k]))
        lines.append("        'load_order': [")
        for g in t["load_order"]:
            lines.append("            %r," % (tuple(g),))
        lines.append("        ],")
        lines.append("        'textures': [")
        for r in t["textures"]:
            d = dict((k, r[k]) for k in ROW_KEYS)
            for k in OPTIONAL_KEYS:
                if r.get(k):
                    d[k] = r[k]
            lines.append("            %r," % (d,))
        lines.append("        ],")
        lines.append("        'not_touched': [")
        for p, why in t["not_touched"]:
            lines.append("            %r," % ((p, why),))
        lines.append("        ],")
        lines.append("        'not_touched_patterns': [")
        for p, why in NOT_TOUCHED_RX:
            lines.append("            %r," % ((p, why),))
        lines.append("        ],")
        lines.append("    },")
    lines.append("}")
    lines.append("")
    lines.append("CATALOG = {")
    for k in sorted(catalog):
        lines.append("    %r: %r," % (k, catalog[k]))
    lines.append("}")
    return "\n".join(lines) + "\n"


def tables_from_facts(all_facts, games=None):
    games = games or GAMES
    tables, rows_by_game, problems = {}, {}, []
    for appid, facts in all_facts.items():
        g = games[appid]
        try:
            rows, _ = analyse(g, facts, appid)
        except TableError as exc:
            problems.append("%s: %s" % (appid, exc))
            continue
        rows_by_game[appid] = rows
        tables[appid] = {
            "title": g["title"], "build": g["build"], "mount": g["mount"],
            "load_order": [(x["name"], x["entries"], x["size"]) for x in facts["gros"]],
            "textures": rows, "not_touched": sorted(g["not_touched"]),
        }
    return tables, rows_by_game, problems


# --------------------------------------------------------------------------- report and sheet (scratch only)


def report_lines(games, all_facts, tables, cands, top, cands_by_key):
    L = []
    for appid in sorted(tables):
        g, f, t = games[appid], all_facts[appid], tables[appid]
        rows = t["textures"]
        L.append("=" * 100)
        L.append("%s (%s) build %s, manifest build %s" % (g["title"], appid, g["build"], f.get("manifest", {}).get("buildid")))
        L.append("load order (sorted names; the game's own log %s: %s):" % (
            f.get("log"), "same order, same entry counts" if f.get("log_matches_sorted_order") and f.get("log_entry_counts_match") else "DIFFERENT"))
        for x in f["gros"]:
            L.append("   %-34s %6d entries %12d bytes" % (x["name"], x["entries"], x["size"]))
        L.append("")
        L.append("table: %d rows" % len(rows))
        cnt = {}
        for r in rows:
            cnt.setdefault((r["action"], r["role"], "%sx%s" % (r["width"], r["height"]), r["fmt"], r["gro"]), []).append(r["id"].split("/")[-1])
        for k, v in sorted(cnt.items()):
            L.append("   %-12s %-20s %-9s %-8s wins=%-24s x%d  %s" % (k + (len(v), ", ".join(v[:4]) + (" ..." if len(v) > 4 else ""))))
        multi = [r for r in rows if len(r["layers"]) > 1]
        L.append("rows present in more than one archive (the last wins): %s" % (
            "; ".join("%s %s" % (r["id"].split("/")[-1], r["layers"]) for r in multi) or "none"))
        L.append("rows croteam_tex cannot rewrite: %s" % (", ".join("%s(%s)" % (r["id"].split("/")[-1], r["fmt"]) for r in rows if not r["writable"]) or "none"))
        for r in rows:
            if r["kind"] == "layout":
                L.append("layout %s wins in %s (%d bytes); gadgets (name class id @offset  x y w h):" % (r["path"], r["gro"], r["size"]))
                for n, k, i, off, rect in r["layout"]:
                    L.append("   %-24s %3d %3d @%-5d %s" % (n, k, i, off, rect))
            if r["kind"] == "atlas":
                L.append("atlas %s wins in %s: %d bytes, textures %s" % (r["path"], r["gro"], r["size"], r["atlas_textures"]))
                for p in r["atlas_parts"]:
                    L.append("   prompt part %s role=%s group %d rect(x,y,w,h)=%s" % p)
        L.append("")
        L.append("references (resources of the winning layers that name a table texture):")
        for r in rows:
            if r.get("referenced_by"):
                L.append("   %-44s <- %s" % (r["id"].split("Textures/")[-1], "; ".join(r["referenced_by"])))
        no_ref = [r["id"].split("Textures/")[-1] for r in rows if not r.get("referenced_by") and r["kind"] not in ("atlas", "layout")]
        L.append("   no .rsc names them (code or atlas draws them): %s" % ", ".join(no_ref))
        L.append("")
        L.append("unlisted candidates in Textures/Menu|Interface (neither in the table nor excused):")
        for path, layers, size in cands[appid]["unlisted"]:
            L.append("   %-80s %7d %s" % (path, size, layers))
        L.append("")
        L.append("references to prompt-like names from OTHER resources (all layers; * = winning layer):")
        for h in f["grep_hits"]:
            if h["job"] == "refs" and h["hits"].get("tex") and not h["name"].lower().endswith(".tbl"):
                L.append("   %s%s:%s  %s" % ("*" if h.get("active") else " ", f["gros"][h["gro"]]["name"], h["name"],
                                            ", ".join(m.split("/")[-1] for m in h["hits"]["tex"])))
        L.append("fonts (.fnt) that name a texture whose name looks like a button:")
        for h in f["grep_hits"]:
            if h["job"] == "fonts":
                hit = [m for m in h["hits"].get("tex", []) if re.search(r"button|xbox|pad|trigger|shoulder|stick", m, re.I)]
                if hit:
                    L.append("   %s%s:%s -> %s" % ("*" if h.get("active") else " ", f["gros"][h["gro"]]["name"], h["name"], ", ".join(m.split("/")[-1] for m in hit)))
        nofnt = [h for h in f["grep_hits"] if h["job"] == "fonts"]
        L.append("   (%d fonts name textures at all)" % len(nofnt))
        L.append("executable strings (%s):" % g["exe"])
        for b in f["bin_hits"]:
            for label, vals in sorted(b.get("hits", {}).items()):
                L.append("   %s: %s" % (label, " ".join(v.split(":", 1)[1] for v in vals)))
        L.append("")
        L.append("catalog (find --style ps; top result and verdict, then the next candidates):")
        for key in sorted(k for k in top if any(r["catalog_role"] == k[0] and "%dx%d" % (r["width"] or 0, r["height"] or 0) == k[1] for r in rows)):
            L.append("   %-14s %-9s %s  [%s]" % (key[0], key[1], top[key][0], top[key][1]))
            for vid, kind, hint in cands_by_key.get(key, [])[1:4]:
                L.append("   %24s also %s [%s] %s" % ("", vid, kind, hint))
    return L


def make_sheet(all_facts, tables, out_path):
    from PIL import Image, ImageDraw
    tiles = []
    for appid in sorted(tables):
        for r in tables[appid]["textures"]:
            if r["kind"] in ("atlas", "layout"):
                continue
            data = fetched_bytes(all_facts[appid], r["path"])
            label = "%s %s %sx%s %s" % (appid, r["path"].rsplit("/", 1)[-1][:-4], r["width"], r["height"], r["fmt"])
            try:
                t = T.parse(data)
                if not t.supported:
                    raise T.TexError("no decoder")
                rgba, w, h = t.pixels()
                im = Image.frombytes("RGBA", (w, h), rgba)
            except T.TexError:
                im = Image.new("RGBA", (64, 64), (90, 0, 0, 255))
            tiles.append((label, im))
    cell, cols = 160, 8
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 14)), (0x46, 0x49, 0x5A))
    dr = ImageDraw.Draw(sheet)
    for i, (label, im) in enumerate(tiles):
        sc = max(1, min(cell // im.width, cell // im.height))
        if im.width > cell or im.height > cell:
            f = max(im.width, im.height) / cell
            im = im.resize((max(1, int(im.width / f)), max(1, int(im.height / f))), Image.NEAREST)
            sc = 1
        big = im.resize((im.width * sc, im.height * sc), Image.NEAREST) if sc > 1 else im
        x, y = (i % cols) * cell, (i // cols) * (cell + 14)
        sheet.paste(big, (x, y + 14), big)
        dr.text((x + 2, y + 2), label[:30], fill=(255, 255, 255))
    sheet.save(out_path)


# --------------------------------------------------------------------------- command line


def _outside_repo(path, what):
    real = os.path.realpath(path)
    if real == str(ROOT) or real.startswith(str(ROOT) + os.sep) or real.startswith(str(ROOT.parent) + os.sep):
        sys.exit("%s %s is inside the repository: facts hold game bytes, keep them in a scratch folder" % (what, path))


def cmd_build(args):
    dirs = dict(x.split("=", 1) for x in args.dir)
    builds = dict(x.split("=", 1) for x in args.build)
    appids = args.appid or sorted(GAMES)
    all_facts = {}
    for appid in appids:
        if args.facts_in:
            all_facts[appid] = json.load(open(os.path.join(args.facts_in, "facts_%s.json" % appid)))
            continue
        all_facts[appid] = collect(make_request(GAMES[appid], appid), dirs.get(appid), builds.get(appid, GAMES[appid]["build"]))
        if args.facts_out:
            _outside_repo(args.facts_out, "--facts-out")
            os.makedirs(args.facts_out, exist_ok=True)
            with open(os.path.join(args.facts_out, "facts_%s.json" % appid), "w") as f:
                json.dump(all_facts[appid], f, sort_keys=True)
    tables, rows_by_game, problems = tables_from_facts(all_facts)
    if problems:
        sys.stderr.write("REFUSED, nothing written:\n" + "\n".join(problems) + "\n")
        return 1
    top, cands_by_key = catalog_top([r for rows in rows_by_game.values() for r in rows])
    catalog = dict(("%s %s" % k, v) for k, v in top.items())
    text = render_module(tables, catalog)
    compile(text, "serious_tables.py", "exec")
    if args.out == "-":
        sys.stdout.write(text)
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        sys.stderr.write("wrote %s (%d bytes)\n" % (args.out, len(text)))
    if args.report or args.sheet:
        cands = {a: {"unlisted": unlisted_candidates(GAMES[a], all_facts[a])} for a in tables}
        if args.report:
            _outside_repo(args.report, "--report")
            with open(args.report, "w", encoding="utf-8") as f:
                f.write("\n".join(report_lines(GAMES, all_facts, tables, cands, top, cands_by_key)) + "\n")
        if args.sheet:
            _outside_repo(args.sheet, "--sheet")
            make_sheet(all_facts, tables, args.sheet)
    return 0


def cmd_scan(args):
    req = json.load(open(args.request))
    req["appid"] = args.appid
    facts = collect(req, args.dir)
    _outside_repo(args.out, "--out")
    with open(args.out, "w") as f:
        json.dump(facts, f, sort_keys=True)
    print("facts: %s (%d gros, %d listed names, %d fetched)" % (args.out, len(facts["gros"]), len(facts["listing"]), len(facts["fetched"])))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="read the games and write the generated table module")
    b.add_argument("--appid", action="append", help="41000 41010 204340 (default: all three)")
    b.add_argument("--dir", action="append", default=[], metavar="APPID=DIR", help="read a local copy of the install folder instead of the Deck")
    b.add_argument("--build", action="append", default=[], metavar="APPID=ID", help="build id to record for a --dir copy")
    b.add_argument("--out", default=str(OUT_PY), help="module to write ('-' = stdout)")
    b.add_argument("--report", help="text report (scratch folder)")
    b.add_argument("--sheet", help="contact sheet PNG (scratch folder, needs Pillow)")
    b.add_argument("--facts-out", help="folder (outside the repo) to keep the raw facts per game: they contain game bytes")
    b.add_argument("--facts-in", help="folder with facts_<appid>.json from an earlier run: no Deck, no game files")
    s = sub.add_parser("scan", help="raw facts for one game and one request file (exploring)")
    s.add_argument("--appid", required=True)
    s.add_argument("--request", required=True)
    s.add_argument("--dir")
    s.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    return {"build": cmd_build, "scan": cmd_scan}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
