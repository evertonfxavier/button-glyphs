#!/usr/bin/env python3
"""READ-ONLY triage of a game's install folder: engine, containers, textures and where the button prompts may be.

    python3 tools/triage_game.py <install dir> [--json out.json] [--max-files N] [--keywords a,b,c]
    python3 tools/triage_game.py --appid <id> [--json out.json]          (host mode: reads the Deck)

It answers, in one JSON object and 25 lines of text, the questions a recon spends most of its tokens on: which
engine, which container formats (by magic bytes, largest first), which texture formats, which files have prompt-like
names (ranked), which names inside archives that can be listed without decompressing anything, whether an
Xbox / PlayStation / Switch variant set already exists, and which patch strategies the evidence points to. Every
conclusion is a lead ("likely", "possible", "check"), never a fact: the recon agent still looks at the real files.

Two modes live in this file:

  Local mode   `triage_game.py <dir>`: a Mac copy or a folder on the Deck.
  Host mode    `--appid <id>`: runs `.claude/scripts/deck.sh --check` (exit 2 when the Deck cannot be reached), then
               sends the REMOTE part below, with the plugin's own steam.py and a tiny JSON request (the appid and the
               limits, no path), on STDIN of `deck.sh 'python3 -'`. The command line therefore names no game path
               (the guard hook scans command text). The Deck finds the install through its Steam libraries.

Read-only and bounded, by construction (tests spy on every write primitive and on every byte read):
  * never writes, never follows a symlink (a link is counted and listed, not opened, not descended), never calls
    anything but stat/scandir/pread; the walk stops at --max-files entries and at --time-budget seconds
    (reported as `truncated`), directories deeper than 64 levels are not entered;
  * a file is opened for 64 bytes of magic. A few bounded extras exist, each with a constant below: 512 bytes of
    header for at most DETAIL_PER_KIND sampled files per texture kind (DDS fourcc, XNB surface format, PNG size) and
    for the ranked candidates; 4 KB of the first bytes of the three largest containers (entropy); small text files
    (<= TEXT_CAP) for a key=value look at config files; the table of contents of a CPK, forge or zip, or the .xml index
    next to an .rmdp, at most --probe-kb KB each (default 8 MB). No file is ever hashed or read whole.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ===== REMOTE BEGIN (self-contained; this text is what runs on the Deck) =====
import json
import math
import os
import re
import stat
import struct
import sys
import time
import types

SCHEMA = 1
MAGIC_BYTES = 64              # every opened file: this many bytes of magic
HEADER_BYTES = 512            # texture header probe (DDS fourcc, XNB reader + surface format, PNG IHDR)
DETAIL_PER_KIND = 20          # sampled files per texture kind that get the header probe
ENTROPY_BYTES = 4096          # first bytes of each of the largest containers
ENTROPY_FILES = 3
ENTROPY_LIMIT = 7.5           # bits per byte above which a sample is "likely encrypted or compressed"
TEXT_CAP = 256 << 10          # a text/config file bigger than this is not opened
TEXT_READ = 64 << 10          # at most this much of one text file
TEXT_FILES = 60
SAMPLE_PER_EXT = 30
SAMPLE_EXTS = 80
SIZE_BLOB = 64 << 20          # a file this big is a container candidate whatever its extension
MAX_CONTAINER_SNIFF = 400
MAX_DEPTH = 64
TOP_CANDIDATES = 60
PER_FOLDER = 6                # first pass of the ranked list: at most this many per folder, so no big set hides the rest
MAX_NAMES = 60
MAX_LIST_ARCHIVES = 8         # archives of one format whose names are listed
DEFAULT_MAX_FILES = 200000
DEFAULT_BUDGET_S = 60.0
DEFAULT_PROBE_KB = 8192

DEFAULT_KEYWORDS = ("xbox", "xinput", "gamepad", "controller", "joypad", "button", "btn", "prompt", "glyph",
                    "tooltip", "hud", "ui/", "icon", "a_button", "ps4", "ps5", "dualshock", "dualsense",
                    "playstation", "switch", "nintendo", "keyboard", "input", "ctrl", "pad", "hint", "tutorial",
                    "help")
KEYWORD_WEIGHT = {"xbox": 3, "xinput": 3, "gamepad": 3, "controller": 3, "joypad": 3, "button": 3, "btn": 3,
                  "prompt": 3, "glyph": 3, "a_button": 3, "ps4": 3, "ps5": 3, "dualshock": 3, "dualsense": 3,
                  "playstation": 3, "tooltip": 3, "switch": 2, "nintendo": 2, "keyboard": 2, "hint": 2,
                  "tutorial": 2, "ctrl": 2, "input": 2, "hud": 1, "ui/": 1, "icon": 1, "pad": 1, "help": 1}
EXACT_ONLY = {"pad", "ctrl", "hud", "btn"}       # short words that must be a whole word (pad1, pads ok; padding no)

IMAGE_EXTS = {"png", "dds", "tga", "xnb", "tex", "jpg", "jpeg", "bmp", "ktx", "ktx2", "pvr", "astc", "gif", "webp",
              "svg", "emb", "texture", "sprite", "atlas", "psd"}
NOT_PROMPT_EXTS = {"ogg", "wav", "mp3", "flac", "mp4", "webm", "bik", "bk2", "usm", "avi", "wmv", "m4a", "wem", "fsb",
                   "bnk", "dll", "so", "exe", "pdb", "lib", "a", "dylib", "o", "obj", "bin", "dat", "mpg", "ttf",
                   "otf", "fon"}
UI_WORDS = {"ui", "gui", "hud", "menu", "menus", "tooltip", "tooltips", "icons", "icon", "interface", "buttons",
            "prompts", "controls", "input", "hints", "pause", "options", "textbox", "textures2d"}
ARCHIVE_EXTS = {"cpk", "forge", "rmdp", "pak", "utoc", "ucas", "pck", "vpk", "pk3", "pk4", "bsa", "ba2", "cas", "cat",
                "toc", "asar", "wad", "rpa", "rgss3a", "rgssad", "bundle", "assets", "win", "unx", "tar", "zip", "7z",
                "rar", "big", "mpq", "arc", "npk", "pkg", "emb", "bank", "fsb", "resource", "resS", "unity3d", "nw",
                "love", "apk", "jar", "gz", "xz", "zst", "pak2", "sarc", "cab", "vol", "dpk"}
CONFIG_EXTS = {"ini", "cfg", "conf", "json", "xml", "txt", "yaml", "yml", "toml", "lua", "properties", "settings",
               "prefs", "plist", "sav", "save", "config", "vdf"}
CONFIG_KEY = re.compile(r"(?i)^\s*[\"']?([\w .\-]*(?:button|controller|gamepad|joypad|prompt|glyph|xinput|"
                        r"input|pad)[\w .\-]*)[\"']?\s*[=:]\s*(.{0,40})")
INPUT_NAMES = re.compile(r"(?i)^(xinput[\w.\-]*|dinput[\w.\-]*|sdl2?[\w.\-]*|libsdl[\w.\-]*|steam_api[\w.\-]*|"
                         r"steamclient[\w.\-]*|gamecontrollerdb[\w.\-]*|controller[\w.\-]*\.vdf|game_actions[\w.\-]*|"
                         r"rewired[\w.\-]*|incontrol[\w.\-]*|inputsystem[\w.\-]*|xinputdotnet[\w.\-]*|"
                         r"steaminput[\w.\-]*|openxr[\w.\-]*|glfw[\w.\-]*)$")

VARIANTS = {"xbox": {"xbox", "xbox360", "xboxone", "xboxseries", "x360", "xb1", "xone"},
            "ps": {"ps3", "ps4", "ps5", "dualshock", "dualshock4", "dualsense", "playstation", "ds4"},
            "switch": {"switch", "nintendo", "joycon"}}
VARIANT_OF = {tok: fam for fam, toks in VARIANTS.items() for tok in toks}

# (prefix, offset, kind, category, extensions that make it count (None: any))
MAGICS = [
    (b"CPK ", 0, "cpk", "archive", None),
    (b"scimitar", 0, "forge", "archive", None),
    (b"UnityFS", 0, "unityfs", "archive", None),
    (b"UnityWeb", 0, "unityweb", "archive", None),
    (b"UnityRaw", 0, "unityraw", "archive", None),
    (b"GDPC", 0, "godot-pck", "archive", None),
    (b"PK\x03\x04", 0, "zip", "archive", None),
    (b"PK\x05\x06", 0, "zip", "archive", None),
    (b"Rar!", 0, "rar", "archive", None),
    (b"7z\xbc\xaf\x27\x1c", 0, "7z", "archive", None),
    (b"\x1f\x8b", 0, "gzip", "archive", None),
    (b"BZh", 0, "bzip2", "archive", {"bz2", "bzip2", "tbz", "tbz2"}),
    (b"\xfd7zXZ\x00", 0, "xz", "archive", None),
    (b"\x28\xb5\x2f\xfd", 0, "zstd", "archive", None),
    (b"\x34\x12\xaa\x55", 0, "vpk", "archive", None),
    (b"PACK", 0, "quake-pak", "archive", {"pak", "pk4"}),
    (b"IWAD", 0, "wad", "archive", None),
    (b"PWAD", 0, "wad", "archive", None),
    (b"BSA\x00", 0, "bsa", "archive", None),
    (b"BTDX", 0, "ba2", "archive", None),
    (b"-==--==--==--==-", 0, "ue-iostore-toc", "archive", None),
    (b"NyanNyanNyanNyan", 0, "frostbite-cat", "archive", None),
    (b"#EMB", 0, "emb", "archive", None),
    (b"RPA-", 0, "rpa", "archive", None),
    (b"RGSSAD\x00", 0, "rgssad", "archive", None),
    (b"TES4", 0, "bethesda-plugin", "other", None),
    (b"TES3", 0, "bethesda-plugin", "other", None),
    (b"FORM", 0, "iff-form", "archive", None),
    (b"XNB", 0, "xnb", "texture", None),
    (b"DDS ", 0, "dds", "texture", None),
    (b"\x89PNG\r\n\x1a\n", 0, "png", "texture", None),
    (b"\xabKTX 11", 0, "ktx", "texture", None),
    (b"\xabKTX 20", 0, "ktx2", "texture", None),
    (b"\xff\xd8\xff", 0, "jpeg", "texture", None),
    (b"BM", 0, "bmp", "texture", {"bmp"}),
    (b"GIF8", 0, "gif", "texture", None),
    (b"PVR\x03", 0, "pvr", "texture", None),
    (b"\x13\xab\xa1\x5c", 0, "astc", "texture", None),
    (b"OggS", 0, "ogg", "audio", None),
    (b"fLaC", 0, "flac", "audio", None),
    (b"ID3", 0, "mp3", "audio", {"mp3"}),
    (b"FSB5", 0, "fsb5", "audio", None),
    (b"BKHD", 0, "wwise-bnk", "audio", None),
    (b"RIFF", 0, "riff", "audio", None),
    (b"MZ", 0, "pe", "exe", None),
    (b"\x7fELF", 0, "elf", "exe", None),
    (b"SQLite format 3", 0, "sqlite", "other", None),
]
SURFACE = {0: "Color(RGBA8)", 1: "Bgr565", 2: "Bgra5551", 3: "Bgra4444", 4: "Dxt1", 5: "Dxt3", 6: "Dxt5",
           7: "NormalizedByte2", 8: "NormalizedByte4", 9: "Rgba1010102", 10: "Rg32", 11: "Rgba64", 12: "Alpha8",
           13: "Single", 14: "Vector2", 15: "Vector4", 16: "HalfSingle", 17: "HalfVector2", 18: "HalfVector4",
           19: "HdrBlendable"}
DXGI = {28: "RGBA8", 71: "BC1", 74: "BC2", 77: "BC3", 80: "BC4", 83: "BC5", 87: "BGRA8", 95: "BC6H", 98: "BC7"}
CONF_HIGH, CONF_MEDIUM = 5, 3


def _clean(text):
    return text.encode("utf-8", "replace").decode("utf-8")


def _norm(rel):
    return rel.replace("\\", "/")


def _ext(name):
    base = name.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base.strip(".") else ""


class Meter(object):
    """Counts what the run really opens and reads, so a test (and the report) can prove the bounds."""

    def __init__(self, clock, deadline):
        self.clock, self.deadline = clock, deadline
        self.opens = self.bytes = 0
        self.timed_out = False

    def expired(self):
        if self.timed_out:
            return True
        if self.clock() > self.deadline:
            self.timed_out = True
        return self.timed_out

    def read(self, path, offset, n):
        """At most n bytes at offset, through no link; b'' for anything that is not a regular file."""
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError:
            return b""
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return b""
            self.opens += 1
            out = b""
            while len(out) < n:
                block = os.pread(fd, n - len(out), offset + len(out))
                if not block:
                    break
                out += block
            self.bytes += len(out)
            return out
        except OSError:
            return b""
        finally:
            os.close(fd)


# ---------------------------------------------------------------- the walk
def walk_tree(root, max_entries, meter):
    """Alphabetical depth-first walk. Links are recorded, never followed. Returns (files, dirs, links, stats)."""
    files, dirs, links = [], [], []
    st = {"unreadable": 0, "special": 0, "truncated_files": False, "last_dir": "", "entries": 0, "link_count": 0}
    stack = [("", root, 0)]
    stop = False
    while stack and not stop:
        rel, path, depth = stack.pop()
        st["last_dir"] = rel
        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            st["unreadable"] += 1
            continue
        subdirs = []
        for e in entries:
            if st["entries"] >= max_entries:
                st["truncated_files"] = True
                stop = True
                break
            st["entries"] += 1
            if (st["entries"] & 15) == 0 and meter.expired():
                stop = True
                break
            erel = rel + "/" + e.name if rel else e.name
            try:
                if e.is_symlink():
                    st["link_count"] += 1
                    if len(links) < 20:
                        links.append(erel)
                    continue
                if e.is_dir(follow_symlinks=False):
                    dirs.append(erel)
                    if depth < MAX_DEPTH:
                        subdirs.append((erel, e.path, depth + 1))
                elif e.is_file(follow_symlinks=False):
                    files.append((erel, e.stat(follow_symlinks=False).st_size))
                else:
                    st["special"] += 1
            except OSError:
                st["unreadable"] += 1
        for item in reversed(subdirs):
            stack.append(item)
    return files, dirs, links, st


class Index(object):
    def __init__(self, files, dirs):
        self.files = files
        self.dirs = dirs
        self.size = dict(files)
        self.low = {}
        self.by_base = {}
        self.by_ext = {}
        self.ext_bytes = {}
        for rel, size in files:
            low = rel.lower()
            self.low[rel] = low
            self.by_base.setdefault(low.rsplit("/", 1)[-1], []).append(rel)
            ext = _ext(low) or "(none)"
            self.by_ext.setdefault(ext, []).append(rel)
            self.ext_bytes[ext] = self.ext_bytes.get(ext, 0) + size
        self.dir_low = set(d.lower() for d in dirs)
        self.dir_base = set(d.lower().rsplit("/", 1)[-1] for d in dirs)
        self.all_low = set(self.low.values())

    def has(self, base):
        return base in self.by_base

    def first(self, base):
        return sorted(self.by_base[base])[0] if base in self.by_base else None

    def ext_files(self, ext):
        return sorted(self.by_ext.get(ext, []))

    def has_dir(self, base):
        return base in self.dir_base

    def match(self, pattern):
        rx = re.compile(pattern)
        return sorted(rel for rel, low in self.low.items() if rx.search(low))


# ---------------------------------------------------------------- magic and texture details
def classify(head, ext, base):
    """{'kind', 'cat'} from the first bytes (extension only disambiguates short magics)."""
    for prefix, off, kind, cat, exts in MAGICS:
        if head[off:off + len(prefix)] == prefix and (exts is None or ext in exts):
            if kind == "riff":
                sub = head[8:12]
                kind = "wav" if sub == b"WAVE" else "webp" if sub == b"WEBP" else "riff"
                cat = "texture" if kind == "webp" else "audio"
            if kind == "iff-form" and base in ("data.win", "game.unx", "game.ios", "game.droid"):
                kind = "gamemaker-form"
            return {"kind": kind, "cat": cat}
    if ext == "tga" and len(head) >= 18 and head[1] in (0, 1) and head[2] in (1, 2, 3, 9, 10, 11) \
            and head[16] in (8, 15, 16, 24, 32):
        return {"kind": "tga", "cat": "texture"}
    if ext == "tex":
        return {"kind": "tex-unknown", "cat": "texture"}
    return {"kind": "", "cat": ""}


def _varint(data, pos):
    value = shift = 0
    for _ in range(5):
        if pos >= len(data):
            return None, pos
        b = data[pos]
        pos += 1
        value |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return value, pos
    return None, pos


def xnb_detail(data):
    """{'format','w','h'} of an XNB header probe (512 bytes): reader type, surface format, size when it is a Texture2D."""
    out = {"format": "XNB"}
    if len(data) < 12:
        return out
    flags = data[5]
    version = data[4]
    out["format"] = "XNB v%d" % version
    if flags & 0x80 or flags & 0x40:
        out["format"] += " compressed"
    count, pos = _varint(data, 10)
    if count is None or count < 1 or count > 64:
        return out
    readers = []
    for _ in range(count):
        size, pos = _varint(data, pos)
        if size is None or pos + size + 4 > len(data):
            out["format"] += " (readers beyond the header probe)"
            return out
        readers.append(data[pos:pos + size].decode("latin-1").split(",")[0].rsplit(".", 1)[-1])
        pos += size + 4
    root, pos = _varint(data, pos)
    name = readers[root] if root is not None and root < len(readers) else readers[0]
    out["type"] = name.replace("Reader", "")
    if name == "Texture2DReader" and not flags & 0xC0:
        for start in range(pos, pos + 9):
            if start + 20 > len(data):
                break
            surface, w, h, mips, dsz = struct.unpack_from("<iiiii", data, start)
            if 0 <= surface <= 19 and 1 <= w <= 16384 and 1 <= h <= 16384 and 1 <= mips <= 16 and dsz > 0:
                out.update(format="XNB Texture2D %s" % SURFACE.get(surface, surface), w=w, h=h)
                break
        else:
            out["format"] = "XNB Texture2D"
    elif not flags & 0xC0:
        out["format"] = "XNB %s" % out["type"]
    return out


def texture_detail(kind, data):
    out = {}
    try:
        if kind == "dds" and len(data) >= 128:
            h, w = struct.unpack_from("<II", data, 12)
            fourcc = data[84:88]
            if fourcc == b"DX10" and len(data) >= 132:
                dxgi = struct.unpack_from("<I", data, 128)[0]
                fmt = "DDS %s" % DXGI.get(dxgi, "DXGI %d" % dxgi)
            elif fourcc.strip(b"\0") and all(32 <= c < 127 for c in fourcc):
                fmt = "DDS %s" % fourcc.decode("ascii")
            else:
                fmt = "DDS raw %dbpp" % struct.unpack_from("<I", data, 88)[0]
            out = {"format": fmt, "w": w, "h": h}
        elif kind == "png" and len(data) >= 26:
            w, h = struct.unpack_from(">II", data, 16)
            out = {"format": "PNG %dbit" % data[24], "w": w, "h": h}
        elif kind == "tga" and len(data) >= 18:
            w, h = struct.unpack_from("<HH", data, 12)
            out = {"format": "TGA %dbpp" % data[16], "w": w, "h": h}
        elif kind in ("ktx", "ktx2") and len(data) >= 44:
            out = {"format": kind.upper() + (" glInternalFormat 0x%X" % struct.unpack_from("<I", data, 28)[0]
                                              if kind == "ktx" else "")}
        elif kind == "xnb":
            out = xnb_detail(data)
        elif kind == "tex-unknown":
            out = {"format": "tex head=%s (raw?)" % data[:4].hex()}
        elif kind in ("jpeg", "bmp", "gif", "webp", "pvr", "astc"):
            out = {"format": kind.upper()}
    except (struct.error, IndexError):
        pass
    return out


def entropy(data):
    if not data:
        return 0.0
    counts = [0] * 256
    for b in data:
        counts[b] += 1
    n = float(len(data))
    return -sum((c / n) * math.log(c / n, 2) for c in counts if c)


# ---------------------------------------------------------------- keywords and ranking
def _camel(seg):
    return [p.lower() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+", seg)]


def path_tokens(rel):
    toks = set()
    for seg in _norm(rel).split("/"):
        stem = seg.rsplit(".", 1)[0] if "." in seg.strip(".") else seg
        for part in re.split(r"[^A-Za-z0-9]+", stem):
            if part:
                toks.add(part.lower())
                toks.update(_camel(part))
    return toks


def make_matcher(keywords):
    """match(rel) -> sorted matched keywords. A cheap substring screen first, the word rules second."""
    kws = [k.strip().lower() for k in keywords if k.strip()]
    screen = re.compile("|".join(re.escape(k.replace("_", "")) if "_" in k else re.escape(k) for k in kws) or "$^")
    sep = [k for k in kws if not re.fullmatch(r"[a-z0-9]+", k)]

    def one(k, toks):
        if len(k) >= 5:
            return any(k == t or k in t for t in toks)
        for t in toks:
            if t == k:
                return True
            if t.startswith(k):
                rest = t[len(k):]
                if rest.isdigit() or rest in ("s", "es") or (k not in EXACT_ONLY and k != "ui"):
                    return True
        return False

    def match(rel):
        low = _norm(rel).lower()
        flat = re.sub(r"[-_. ]", "", low)
        if not screen.search(low) and not screen.search(flat):
            return []
        toks = path_tokens(rel)
        hits = []
        for k in kws:
            if k in sep:
                norm = "/" + re.sub(r"[-. ]", "_", low)
                if re.search(r"(?<![a-z0-9])" + re.escape(k) + (r"" if k.endswith("/") else r"(?![a-z])"), norm):
                    hits.append(k)
            elif one(k, toks):
                hits.append(k)
        return sorted(set(hits))

    return match


def rank_candidates(ix, keywords, custom):
    match = make_matcher(keywords)
    out = []
    for rel, size in ix.files:
        ext = _ext(rel)
        if ext in NOT_PROMPT_EXTS:
            continue
        words = match(rel)
        if not words:
            continue
        score = sum(2 if custom else KEYWORD_WEIGHT.get(w, 1) for w in words)
        plaus = (2 if ext in IMAGE_EXTS else 1 if ext in CONFIG_EXTS else 0)
        plaus += 2 if size <= 64 << 10 else 1 if size <= 512 << 10 else 0
        plaus += 1 if path_tokens(rel) & UI_WORDS else 0
        out.append({"path": rel, "size": size, "score": score, "words": words, "_p": plaus})
    out.sort(key=lambda c: (-c["score"], -c["_p"], c["path"]))
    return out


def select_candidates(ranked, n=TOP_CANDIDATES, per_folder=PER_FOLDER):
    """The best n candidates: each folder's best few first (rank order), then the rest of the ranking."""
    taken, count, picked = [], {}, set()
    for c in ranked:
        folder = c["path"].rsplit("/", 1)[0] if "/" in c["path"] else ""
        if count.get(folder, 0) < per_folder and len(taken) < n:
            count[folder] = count.get(folder, 0) + 1
            taken.append(c)
            picked.add(c["path"])
    for c in ranked:
        if len(taken) >= n:
            break
        if c["path"] not in picked:
            taken.append(c)
    taken.sort(key=lambda c: (-c["score"], -c["_p"], c["path"]))
    return taken


def candidate_folders(ranked, n=15):
    """Where the ranked candidates cluster: folder, how many, best score, the words seen."""
    rows = {}
    for c in ranked:
        folder = c["path"].rsplit("/", 1)[0] if "/" in c["path"] else "(top level)"
        r = rows.setdefault(folder, {"folder": folder, "count": 0, "best_score": 0, "words": set()})
        r["count"] += 1
        r["best_score"] = max(r["best_score"], c["score"])
        r["words"].update(c["words"])
    out = sorted(rows.values(), key=lambda r: (-r["best_score"], -r["count"], r["folder"]))[:n]
    for r in out:
        r["words"] = sorted(r["words"])
    return out


# ---------------------------------------------------------------- variant sets (xbox / ps / switch by filename)
def variant_sets(ix):
    fam_files = {"xbox": 0, "ps": 0, "switch": 0}
    fam_dirs = {}
    pairs = {}
    for rel, _ in ix.files:
        segs = rel.lower().split("/")
        found, new = set(), []
        for i, seg in enumerate(segs):
            last = i == len(segs) - 1
            stem, dot, ext = seg.rpartition(".") if (last and "." in seg.strip(".")) else (seg, "", "")
            parts = re.split(r"([_\- ])", stem)
            kept = []
            for p in parts:
                fam = VARIANT_OF.get(p)
                if fam:
                    found.add(fam)
                    if kept and kept[-1] in "_- ":
                        kept.pop()
                else:
                    kept.append(p)
            while kept and kept[0] in "_- ":
                kept.pop(0)
            stem2 = "".join(kept)
            if stem2:
                new.append(stem2 + (dot + ext if dot else ""))
        if not found:
            continue
        for fam in found:
            fam_files[fam] += 1
            fam_dirs.setdefault(fam, set()).add("/".join(rel.split("/")[:-1]))
        key = "/".join(new)
        pairs.setdefault(key, set()).update(found)
    for key in list(pairs):
        if key in ix.all_low:
            pairs[key].add("default")
    paired = sorted((k, sorted(v)) for k, v in pairs.items() if len(v) >= 2)
    have_ps_xbox = sum(1 for _, v in paired if "ps" in v and "xbox" in v)
    return {"files_by_family": {k: v for k, v in sorted(fam_files.items()) if v},
            "folders": {k: sorted(v)[:6] for k, v in sorted(fam_dirs.items())},
            "paired": [{"name": k, "families": v} for k, v in paired[:20]],
            "paired_count": len(paired), "ps_and_xbox_pairs": have_ps_xbox}


# ---------------------------------------------------------------- listing names without decompressing
def _utf_rows(buf, off):
    """Rows of the @UTF table at buf[off:] (CPK headers and TOC)."""
    if buf[off:off + 4] != b"@UTF":
        raise ValueError("no @UTF table (encrypted or unknown CPK header)")
    base = off + 8
    rows_off, str_off, data_off, _n, ncol, rowlen, nrows = struct.unpack_from(">IIIIHHI", buf, base)
    scalars = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "Q", 7: "q", 8: "f"}

    def cstr(o):
        s = base + str_off + o
        return buf[s:buf.index(b"\0", s)].decode("utf-8", "replace")

    def value(p, ty):
        if ty in scalars:
            fmt = ">" + scalars[ty]
            return struct.unpack_from(fmt, buf, p)[0], p + struct.calcsize(fmt)
        if ty == 0xA:
            return cstr(struct.unpack_from(">I", buf, p)[0]), p + 4
        if ty == 0xB:
            return None, p + 8
        raise ValueError("unknown @UTF column type")

    p = base + 24
    cols = []
    for _ in range(ncol):
        flags = buf[p]
        name = cstr(struct.unpack_from(">I", buf, p + 1)[0])
        p += 5
        storage, ty = flags & 0xF0, flags & 0x0F
        const = None
        if storage == 0x30:
            const, p = value(p, ty)
        cols.append((name, storage, ty, const))
    if nrows > 500000:
        raise ValueError("implausible row count")
    rows = []
    for r in range(nrows):
        q = base + rows_off + r * rowlen
        row = {}
        for name, storage, ty, const in cols:
            if storage == 0x50:
                row[name], q = value(q, ty)
            else:
                row[name] = const
        rows.append(row)
    return rows


def list_cpk(path, size, meter, cap):
    head = meter.read(path, 0, 0x800)
    if head[:4] != b"CPK ":
        return None, "not a CPK"
    h = _utf_rows(head, 16)[0]
    toc_off, toc_size = h.get("TocOffset"), h.get("TocSize")
    if not toc_off or not toc_size:
        return None, "CPK without a TOC (ITOC-only or extended layout)"
    if toc_size > cap:
        return None, "TOC of %d KB is above the %d KB probe cap" % (toc_size >> 10, cap >> 10)
    toc = meter.read(path, toc_off, toc_size + 16)
    if toc[:4] != b"TOC ":
        return None, "TOC chunk not found at TocOffset"
    names = ["%s/%s" % (r["DirName"], r["FileName"]) if r.get("DirName") else r["FileName"]
             for r in _utf_rows(toc, 16)]
    return names, ""


def list_forge(path, size, meter, cap):
    head = meter.read(path, 0, 0x40)
    if head[:8] != b"scimitar":
        return None, "not a scimitar forge"
    version, hdr_off = struct.unpack_from("<iq", head, 9)
    first = struct.unpack_from("<q", meter.read(path, hdr_off, 0x28), 0x20)[0]
    names, off, guard, spent = [], first, 0, 0
    while off not in (-1, 0):
        guard += 1
        if guard > 1000:
            return None, "block chain loops"
        _, _, idx, nxt, a, b, noff, _ = struct.unpack("<iiqqiiqq", meter.read(path, off, 0x30))
        n = b - a + 1
        if n <= 0 or n > 200000 or spent + 188 * n > cap:
            return None, "name table above the %d KB probe cap or implausible" % (cap >> 10)
        index = meter.read(path, idx, 16 * n)
        raw = meter.read(path, noff, 188 * n)
        spent += 188 * n
        for i in range(n):
            o, _fid, sz = struct.unpack_from("<qII", index, 16 * i)
            if o == 0 and sz == 0:
                continue
            names.append(raw[188 * i + 44:188 * i + 172].split(b"\0")[0].decode("latin-1"))
        off = nxt
    return names, ""


def list_zip(path, size, meter, cap):
    tail_n = min(size, 66000)
    tail = meter.read(path, size - tail_n, tail_n)
    i = tail.rfind(b"PK\x05\x06")
    if i < 0 or i + 22 > len(tail):
        return None, "no end-of-central-directory record"
    total, cd_size, cd_off = struct.unpack_from("<HII", tail, i + 10)
    if cd_off == 0xFFFFFFFF or total == 0xFFFF:
        return None, "zip64 archive"
    if cd_size > cap:
        return None, "central directory above the probe cap"
    cd = meter.read(path, cd_off, cd_size)
    names, p = [], 0
    while p + 46 <= len(cd) and cd[p:p + 4] == b"PK\x01\x02" and len(names) < 500000:
        n, e, c = struct.unpack_from("<HHH", cd, p + 28)
        names.append(cd[p + 46:p + 46 + n].decode("utf-8", "replace"))
        p += 46 + n + e + c
    return names, ""


def list_xml_index(path, size, meter, cap):
    """Names in the plain-text .xml index that sits next to a Northlight .rmdp (file/name/path attributes)."""
    attr = re.compile(rb"(?:name|path|file|filename)\s*=\s*[\"']([^\"'<>]{3,260})[\"']", re.I)
    text = re.compile(rb">([^<>\s][^<>]{2,260}\.[A-Za-z0-9]{2,6})<")
    names, seen, off, tail = [], set(), 0, b""
    limit = min(size, cap)
    while off < limit:
        block = meter.read(path, off, min(1 << 20, limit - off))
        if not block:
            break
        buf = tail + block
        for rx in (attr, text):
            for m in rx.finditer(buf):
                s = m.group(1).decode("utf-8", "replace")
                if s not in seen and len(seen) < 500000:
                    seen.add(s)
                    names.append(s)
        tail = buf[-300:]
        off += len(block)
    note = "" if size <= cap else "xml index read only up to the first %d KB" % (cap >> 10)
    if not names:
        return None, "xml index has no recognised name attribute"
    return names, note


LISTERS = {"cpk": list_cpk, "forge": list_forge, "zip": list_zip}


def archive_names(ix, kinds, containers, match, meter, cap):
    """Names inside archives that can be listed cheaply, matching the keywords (cap MAX_NAMES overall)."""
    out, not_listable, budget = [], {}, MAX_NAMES
    todo = []
    for rel, size in sorted(ix.files, key=lambda f: (-f[1], f[0])):
        k = kinds.get(rel, {}).get("kind", "")
        ext = _ext(rel)
        if k in LISTERS:
            todo.append((rel, size, k))
        elif ext == "rmdp":
            stem = rel[:-5]
            for cand in (stem + ".xml", stem + ".XML"):
                if cand in ix.size:
                    todo.append((rel, ix.size[cand], "rmdp+xml", cand))
                    break
            else:
                not_listable["rmdp (no xml index next to it)"] = not_listable.get("rmdp (no xml index next to it)", 0) + 1
        elif k in ("unityfs", "ue-iostore-toc", "godot-pck", "vpk", "bsa", "ba2", "quake-pak", "wad", "rpa",
                   "frostbite-cat") or ext in ("pak", "utoc", "ucas", "asar", "bundle", "assets"):
            key = k or ext
            not_listable[key] = not_listable.get(key, 0) + 1
    per_fmt = {}
    for item in todo:
        if meter.expired():
            break
        rel, size, fmt = item[0], item[1], item[2]
        if per_fmt.get(fmt, 0) >= MAX_LIST_ARCHIVES:
            continue
        per_fmt[fmt] = per_fmt.get(fmt, 0) + 1
        row = {"archive": rel, "format": fmt, "listable": False}
        try:
            if fmt == "rmdp+xml":
                names, note = list_xml_index(os.path.join(meter.root, *item[3].split("/")), item[1], meter, cap)
            else:
                names, note = LISTERS[fmt](os.path.join(meter.root, *rel.split("/")), size, meter, cap)
        except (ValueError, struct.error, IndexError, KeyError) as exc:
            names, note = None, "unreadable listing: %s" % exc
        if names is None:
            row["note"] = note
            out.append(row)
            continue
        hits = []
        for nm in names:
            w = match(nm)
            if w:
                hits.append((nm, w))
        exts = {}
        for nm in names:
            e = _ext(nm)
            exts[e or "(none)"] = exts.get(e or "(none)", 0) + 1
        row.update(listable=True, entries=len(names), matching=len(hits), note=note,
                   top_extensions=[[e, c] for e, c in sorted(exts.items(), key=lambda kv: (-kv[1], kv[0]))[:6]],
                   names=[{"name": nm, "words": w} for nm, w in hits[:max(0, min(20, budget))]])
        budget -= len(row["names"])
        out.append(row)
    return out, [{"format": k, "count": v, "note": "not listable without parsing or decompressing"}
                 for k, v in sorted(not_listable.items())]


# ---------------------------------------------------------------- engines
def detect_engines(ix, kinds):
    ev = {}

    def add(engine, pts, text):
        ev.setdefault(engine, []).append((pts, text))

    def kind_files(kind):
        return sorted(r for r, k in kinds.items() if k.get("kind") == kind)

    # Unity
    for base in ("unityplayer.dll", "unityplayer.so", "unityplayer.dylib", "libunity.so", "unitycrashhandler64.exe",
                 "unitycrashhandler32.exe"):
        if ix.has(base):
            add("Unity", 3, ix.first(base))
            break
    data = ix.match(r"(^|/)[^/]*_data/(globalgamemanagers|data\.unity3d|resources\.assets|sharedassets\d+\.assets|"
                    r"level\d+|maindata)$")
    if data:
        add("Unity", 3, "%s (+%d more Unity data files)" % (data[0], len(data) - 1) if len(data) > 1 else data[0])
    if kind_files("unityfs"):
        add("Unity", 2, "UnityFS magic: %s" % kind_files("unityfs")[0])
    if ix.has_dir("streamingassets"):
        add("Unity", 1, "StreamingAssets folder")
    # Unreal
    if ix.has_dir("engine") and (ix.has_dir("binaries") or ix.ext_files("pak") or ix.ext_files("utoc")):
        add("Unreal", 1, "Engine/ folder with Binaries/ or paks")
    if ix.ext_files("pak") and not ix.ext_files("vpk") and (ix.has_dir("paks") or ix.has_dir("engine")):
        add("Unreal", 2, "%d .pak (Content/Paks or Engine/)" % len(ix.ext_files("pak")))
    if ix.ext_files("utoc") or ix.ext_files("ucas"):
        add("Unreal", 3, "IoStore container: %s" % (ix.ext_files("utoc") or ix.ext_files("ucas"))[0])
    if kind_files("ue-iostore-toc"):
        add("Unreal", 2, "IoStore toc magic '-==--==--==--==-' in %s" % kind_files("ue-iostore-toc")[0])
    if ix.match(r"(^|/)binaries/win64(/|$)") or ix.match(r"-(win64|linux)-shipping\.exe$|-(win64|linux)-shipping$"):
        add("Unreal", 3, "Binaries/Win64 or *-Win64-Shipping")
    if ix.ext_files("uproject"):
        add("Unreal", 3, ".uproject")
    # GameMaker
    for base in ("data.win", "game.unx", "game.ios", "game.droid"):
        if ix.has(base):
            add("GameMaker", 3, ix.first(base))
            if any(kinds.get(r, {}).get("kind") == "gamemaker-form" for r in ix.by_base[base]):
                add("GameMaker", 2, "'FORM' magic in %s" % base)
            break
    if ix.match(r"(^|/)audiogroup\d+\.dat$"):
        add("GameMaker", 1, "audiogroup*.dat")
    # MonoGame / XNA / FNA
    xnb = ix.ext_files("xnb")
    if xnb:
        ok = sum(1 for r in xnb if kinds.get(r, {}).get("kind") == "xnb")
        seen = sum(1 for r in xnb if r in kinds)
        if seen >= 3 and ok >= 0.8 * seen:
            add("MonoGame/XNA/FNA", 5, "%d .xnb files, 'XNB' magic on %d/%d sampled (e.g. %s)" % (
                len(xnb), ok, seen, xnb[0]))
        elif len(xnb) >= 10:
            add("MonoGame/XNA/FNA", 2, "%d .xnb files (magic not confirmed on enough samples)" % len(xnb))
        elif xnb:
            add("MonoGame/XNA/FNA", 1, "%d .xnb file(s)" % len(xnb))
    for base, label in (("monogame.framework.dll", "MonoGame.Framework.dll"), ("fna.dll", "FNA.dll"),
                        ("microsoft.xna.framework.dll", "Microsoft.Xna.Framework.dll")):
        if ix.has(base):
            add("MonoGame/XNA/FNA", 3, label)
    # Godot
    pck = kind_files("godot-pck")
    if pck:
        add("Godot", 5, "'GDPC' magic: %s" % pck[0])
    elif ix.ext_files("pck"):
        add("Godot", 2, ".pck without GDPC magic at offset 0 (embedded or not sampled): %s" % ix.ext_files("pck")[0])
    if ix.has("project.godot") or ix.has("project.binary"):
        add("Godot", 3, "project.godot / project.binary")
    if ix.has_dir(".godot") or ix.has_dir(".import"):
        add("Godot", 2, ".godot/ or .import/ folder")
    # RPG Maker
    if ix.has("rpg_core.js") or ix.has("rmmz_core.js"):
        add("RPG Maker", 4, "rpg_core.js / rmmz_core.js (MV/MZ)")
    if ix.has("rpg_rt.exe") or ix.has("rpg_rt.ldb") or ix.has("rpg_rt.lmt"):
        add("RPG Maker", 4, "RPG_RT.* (2000/2003)")
    if ix.ext_files("rgss3a") or ix.ext_files("rgssad") or kind_files("rgssad") or ix.ext_files("rvdata2"):
        add("RPG Maker", 4, "RGSS archive or .rvdata2 (XP/VX/VX Ace)")
    # Ren'Py
    if ix.has_dir("renpy") and (ix.ext_files("rpy") or ix.ext_files("rpyc") or ix.ext_files("rpa")):
        add("Ren'Py", 4, "renpy/ folder with .rpy/.rpyc/.rpa")
    if kind_files("rpa"):
        add("Ren'Py", 3, "'RPA-' magic: %s" % kind_files("rpa")[0])
    # Electron / NW.js
    if ix.has("app.asar"):
        add("Electron", 4, ix.first("app.asar"))
    if ix.match(r"(^|/)resources/app/package\.json$"):
        add("Electron", 3, "resources/app/package.json")
    if ix.has("chrome_100_percent.pak") or ix.has("electron.exe"):
        add("Electron", 2, "Chromium resources (chrome_100_percent.pak / electron.exe)")
    if ix.has("nw.pak") or ix.has("package.nw"):
        add("NW.js", 4, "nw.pak / package.nw")
    # Source / Valve
    if ix.has("gameinfo.txt"):
        add("Source/Valve", 3, ix.first("gameinfo.txt"))
    vpk = ix.ext_files("vpk")
    if vpk:
        add("Source/Valve", 3, ".vpk: %s%s" % (vpk[0], " (VPK magic)" if kind_files("vpk") else ""))
    if kind_files("vpk"):
        add("Source/Valve", 2, "VPK magic 0x55AA1234")
    if ix.ext_files("bsp"):
        add("Source/Valve", 1, ".bsp maps")
    # id Tech / Quake-like
    pk3 = ix.ext_files("pk3") + ix.ext_files("pk4")
    if pk3:
        add("id Tech/Quake-like", 3, "%d .pk3/.pk4 (zip): %s" % (len(pk3), pk3[0]))
    if kind_files("quake-pak"):
        add("id Tech/Quake-like", 3, "Quake PACK magic: %s" % kind_files("quake-pak")[0])
    if kind_files("wad"):
        add("id Tech/Quake-like", 2, "IWAD/PWAD: %s" % kind_files("wad")[0])
    for base in ("baseq3", "id1", "base"):
        if ix.has_dir(base) and (pk3 or kind_files("quake-pak")):
            add("id Tech/Quake-like", 2, "%s/ folder" % base)
            break
    # Remedy Northlight
    rmdp = ix.ext_files("rmdp")
    if rmdp:
        add("Remedy Northlight", 4, "%d .rmdp: %s" % (len(rmdp), rmdp[0]))
        twin = [r for r in rmdp if r[:-5] + ".xml" in ix.size]
        if twin:
            add("Remedy Northlight", 3, "index %s.xml next to the .rmdp" % twin[0][:-5].rsplit("/", 1)[-1])
    # Ubisoft Scimitar / Anvil
    forge = ix.ext_files("forge")
    if forge:
        add("Ubisoft Scimitar/Anvil", 3, "%d .forge: %s" % (len(forge), forge[0]))
    if kind_files("forge"):
        add("Ubisoft Scimitar/Anvil", 3, "'scimitar' magic: %s" % kind_files("forge")[0])
    # CRI / Dimps
    cpk = ix.ext_files("cpk")
    if cpk:
        add("CRI CPK container", 3, "%d .cpk: %s" % (len(cpk), cpk[0]))
    if kind_files("cpk"):
        add("CRI CPK container", 2, "'CPK ' magic: %s" % kind_files("cpk")[0])
    if kind_files("emb"):
        add("Dimps-style EMB bundles", 3, "'#EMB' magic: %s" % kind_files("emb")[0])
    # Bethesda
    ba = kind_files("bsa") + kind_files("ba2")
    if ba:
        add("Bethesda (Creation family)", 4, "BSA/BA2 magic: %s" % ba[0])
    elif ix.ext_files("bsa") or ix.ext_files("ba2"):
        add("Bethesda (Creation family)", 2, ".bsa/.ba2 by extension")
    plug = ix.ext_files("esm") + ix.ext_files("esp") + ix.ext_files("esl")
    if plug:
        add("Bethesda (Creation family)", 3, "plugins: %s%s" % (plug[0], " (TES4 magic)" if kind_files("bethesda-plugin") else ""))
    # Frostbite
    if kind_files("frostbite-cat"):
        add("Frostbite", 3, "cas.cat magic: %s" % kind_files("frostbite-cat")[0])
    if ix.ext_files("cas") and ix.ext_files("cat"):
        add("Frostbite", 2, ".cas + .cat")
    if ix.has("initfs_win32") or ix.has("initfs_win64"):
        add("Frostbite", 3, "initfs_win32/64")
    out = []
    for engine, rows in ev.items():
        score = sum(p for p, _ in rows)
        conf = "high" if score >= CONF_HIGH else "medium" if score >= CONF_MEDIUM else "low"
        out.append({"engine": engine, "score": score, "confidence": conf, "evidence": [t for _, t in rows]})
    out.sort(key=lambda e: (-e["score"], e["engine"]))
    if not out or out[0]["score"] < 2:
        out.insert(0, {"engine": "unknown", "score": 0, "confidence": "none",
                       "evidence": ["no engine signature matched: read the top-level folders and the container list"]})
    return out


def detect_runtimes(ix):
    out = []
    if ix.has("assembly-csharp.dll"):
        out.append({"name": "Mono/.NET managed (Unity script assembly)", "evidence": [ix.first("assembly-csharp.dll")]})
    if ix.has("gameassembly.dll") or ix.has("gameassembly.so"):
        il = ix.match(r"(^|/)il2cpp_data(/|$)")
        out.append({"name": "IL2CPP (C# compiled to native: no IL to edit)",
                    "evidence": [ix.first("gameassembly.dll") or ix.first("gameassembly.so")] + (il[:1] or [])})
    if ix.has_dir("monobleedingedge") or ix.has("mono-2.0-bdwgc.dll") or ix.has_dir("mono"):
        out.append({"name": "Mono runtime", "evidence": ["MonoBleedingEdge/ or mono-2.0-bdwgc.dll"]})
    managed = [b for b in ("mscorlib.dll", "monogame.framework.dll", "fna.dll", "microsoft.xna.framework.dll") if ix.has(b)]
    if managed and not ix.has("assembly-csharp.dll"):
        out.append({"name": ".NET managed game (FNA/XNA/MonoGame style): an IL patch may be possible",
                    "evidence": [ix.first(b) for b in managed]})
    return out


def input_layer(ix):
    rows = []
    for base in sorted(ix.by_base):
        if INPUT_NAMES.match(base):
            rows.append(sorted(ix.by_base[base])[0])
    return rows[:25]


# ---------------------------------------------------------------- config files with a prompt-like key
def config_hits(ix, meter):
    pool = [(rel, size) for rel, size in ix.files if _ext(rel) in CONFIG_EXTS and 0 < size <= TEXT_CAP]
    # shallow files first: a game's own settings sit near the top
    pool.sort(key=lambda f: (f[0].count("/"), f[0]))
    out = []
    for rel, size in pool[:TEXT_FILES]:
        if meter.expired():
            break
        data = meter.read(os.path.join(meter.root, *rel.split("/")), 0, min(size, TEXT_READ))
        if not data:
            continue
        text = data.replace(b"\0", b"").decode("utf-8", "replace")
        lines = []
        for line in text.splitlines():
            m = CONFIG_KEY.match(line[:200])
            if m:
                lines.append(_clean("%s = %s" % (m.group(1).strip()[:50], m.group(2).strip()[:30])))
            if len(lines) >= 5:
                break
        if lines:
            out.append({"path": rel, "lines": lines})
    return out


# ---------------------------------------------------------------- strategy hints
def strategy_hints(rep):
    hints = []

    def add(strategy, likelihood, reason, check):
        hints.append({"strategy": strategy, "likelihood": likelihood, "reason": reason, "check_next": check})

    engines = [e["engine"] for e in rep["engines"] if e["confidence"] in ("high", "medium")]
    vs = rep["variant_sets"]
    fam = vs["files_by_family"]
    cands = rep["candidates"]
    small_images = [c for c in cands if _ext(c["path"]) in IMAGE_EXTS and c["size"] <= 64 << 10]
    kinds = {c["kind"]: c for c in rep["containers"]["kinds"]}
    listed = [a for a in rep["archive_names"] if a.get("listable")]

    if len(fam) >= 2:
        both = "ps" in fam and "xbox" in fam
        add("code-switch (IlPatchProfile) or saved value (ConfigValueProfile)", "likely" if both else "possible",
            "%s variant files by name (%d paired names)%s" % (
                " and ".join("%s x%d" % kv for kv in sorted(fam.items())), vs["paired_count"],
                "" if both else "; no PlayStation set seen, so the game may pick by pad type or only ship one family"),
            "find what chooses the set: pad-type checks in the code (.NET: dnfile + CIL; GameMaker: data.win scripts; "
            "Unity: Assembly-CSharp), then a saved/INI value in the save folder; the engine may already ship PS art")
    elif len(fam) == 1:
        add("loose files or redraw", "possible",
            "only one variant family by name (%s): the other families are probably not shipped" % list(fam)[0],
            "look for a pad-type switch anyway, then plan to supply the missing set")
    if rep["config_hits"]:
        add("saved value (ConfigValueProfile)", "possible",
            "%d config file(s) in the install folder carry a button/controller/input key (%s)" % (
                len(rep["config_hits"]), rep["config_hits"][0]["path"]),
            "see whether the key is a choice of prompt set; saves live in the Proton prefix "
            "(compatdata/<appid>/pfx), which this triage does not read")
    elif "GameMaker" in engines:
        add("saved value (ConfigValueProfile)", "possible",
            "GameMaker games keep options in an INI save in the Proton prefix (10 Second Ninja X did)",
            "diff the save before/after changing the controller option in game; look at data.win sprite names")
    if len(small_images) >= 5 and not any(a.get("listable") and a["matching"] for a in listed):
        loose = sum(1 for c in small_images if _ext(c["path"]) in ("xnb", "png", "dds", "tga", "tex"))
        add("loose files (XnbPictureProfile, files_level)", "likely" if loose >= 5 else "possible",
            "%d small loose image file(s) with prompt-like names; the art is not inside an archive" % len(small_images),
            "do they end exactly at the last pixel (no spare byte for a marker: XNB none, DDS reserved bytes)? "
            "pin SHA-256 + size per file, check the surface format")
    if any(a["format"] == "rmdp+xml" for a in rep["archive_names"]) or "Remedy Northlight" in engines:
        add("in-place raw regions (RegionGlyphProfile)", "likely",
            ".rmdp archive with an .xml index: uncompressed files at fixed offsets (Quantum Break layout)",
            "extract a candidate .tex, check header + raw BGRA, size == offsets in the index, and the .meta CRC")
    if "cpk" in kinds or "CRI CPK container" in engines:
        names = [a for a in listed if a["format"] == "cpk"]
        emb = sum(dict(a.get("top_extensions", [])).get("emb", 0) for a in names)
        add("in-place DXT regions (DxtRegionProfile)", "possible",
            "CRI CPK%s (Saint Seiya layout: .emb bundles of DDS inside a CPK stored uncompressed)" % (
                " with %d .emb entries" % emb if emb else ""),
            "FileSize == ExtractSize in the TOC? .emb members are DDS DXT5? list the matching names")
    if "Ubisoft Scimitar/Anvil" in engines:
        add("entry-level append (ForgeGlyphProfile)", "likely",
            "Scimitar .forge archives: compressed movable entries and an index (AC1 layout)",
            "tools/forge_scan.py on a copy: texture names, formats (code 4 = DXT3), every forge that holds prompts")
    if rep["entropy"] and all(e["verdict"].startswith("likely") for e in rep["entropy"]) and not small_images:
        add("probably not feasible", "possible",
            "the largest containers look encrypted or compressed and no loose prompt images were found",
            "identify the container format and whether the game checks hashes before investing in a strategy")
    elif rep["entropy"] and any(e["verdict"].startswith("likely") for e in rep["entropy"]):
        add("check compression/encryption", "possible",
            "%s looks encrypted or compressed in its first 4 KB" % rep["entropy"][0]["path"],
            "many formats compress on purpose (zip, pak): confirm the format before choosing entry-level vs regions")
    if rep["input_layer"]:
        add("input layer (not a glyph profile)", "possible",
            "input libraries present: %s" % ", ".join(rep["input_layer"][:3]),
            "if controls (not only prompts) are wrong think XInput / Steam Input separately")
    if not hints:
        add("none yet", "unknown", "no evidence matched any strategy",
            "read the top-level folders and the container list; research mods (skill game-research)")
    return hints


# ---------------------------------------------------------------- the triage
def triage(root, max_files=DEFAULT_MAX_FILES, budget_s=DEFAULT_BUDGET_S, keywords=None, probe_kb=DEFAULT_PROBE_KB,
           clock=time.monotonic):
    """One report dict for the folder `root`. Read-only; see the module docstring for the bounds."""
    started = clock()
    custom = bool(keywords)
    kws = list(keywords) if keywords else list(DEFAULT_KEYWORDS)
    given = root
    root = os.path.realpath(root)
    meter = Meter(clock, started + float(budget_s))
    meter.root = root
    cap = int(probe_kb) << 10
    files, dirs, links, st = walk_tree(root, int(max_files), meter)
    ix = Index(files, dirs)
    total_bytes = sum(s for _, s in files)

    # extension histogram
    exts = [(e, len(v), ix.ext_bytes.get(e, 0)) for e, v in ix.by_ext.items()]
    by_count = sorted(exts, key=lambda t: (-t[1], t[0]))[:25]
    by_bytes = sorted(exts, key=lambda t: (-t[2], t[0]))[:25]

    # which files get their 64 magic bytes read
    chosen, seen = [], set()

    def pick(rel):
        if rel not in seen:
            seen.add(rel)
            chosen.append(rel)

    big = sorted(files, key=lambda f: (-f[1], f[0]))
    n_cont = 0
    for rel, size in big:
        if n_cont >= MAX_CONTAINER_SNIFF:
            break
        if _ext(rel) in ARCHIVE_EXTS or size >= SIZE_BLOB or rel.lower().rsplit("/", 1)[-1] in (
                "data.win", "game.unx", "globalgamemanagers", "gameinfo.txt"):
            pick(rel)
            n_cont += 1
    for ext, _count, _b in by_count[:SAMPLE_EXTS]:
        rels = ix.ext_files(ext)
        step = max(1, len(rels) // SAMPLE_PER_EXT)
        for rel in rels[::step][:SAMPLE_PER_EXT]:
            pick(rel)
    ranked = rank_candidates(ix, kws, custom)
    cands_all = select_candidates(ranked)
    for c in cands_all:
        pick(c["path"])

    kinds = {}
    for rel in chosen:
        if meter.expired():
            break
        head = meter.read(os.path.join(root, *rel.split("/")), 0, MAGIC_BYTES)
        if not head:
            continue
        info = classify(head, _ext(rel), rel.lower().rsplit("/", 1)[-1])
        kinds[rel] = info

    # header probe for texture kinds (bounded per kind) and for the ranked candidates
    per_kind, details = {}, {}
    top_set = set(c["path"] for c in cands_all)
    for rel in [c["path"] for c in cands_all] + chosen:
        info = kinds.get(rel)
        if not info or info["cat"] != "texture" or rel in details:
            continue
        top = rel in top_set
        if not top and per_kind.get(info["kind"], 0) >= DETAIL_PER_KIND:
            continue
        if meter.expired():
            break
        per_kind[info["kind"]] = per_kind.get(info["kind"], 0) + 1
        details[rel] = texture_detail(info["kind"], meter.read(os.path.join(root, *rel.split("/")), 0, HEADER_BYTES))

    # containers
    cont = {}
    for rel, info in kinds.items():
        size = ix.size[rel]
        ext = _ext(rel)
        if info["cat"] == "archive" or (not info["cat"] and (ext in ARCHIVE_EXTS or size >= SIZE_BLOB)):
            label = info["kind"] or ("%s (no known magic)" % ext if ext else "large file (no known magic)")
            if info["kind"] == "iff-form":
                label = "iff-form (FORM)"
            row = cont.setdefault(label, {"kind": label, "count": 0, "bytes": 0, "files": []})
            row["count"] += 1
            row["bytes"] += size
            row["files"].append((size, rel))
    container_rows = []
    for row in cont.values():
        row["files"].sort(key=lambda t: (-t[0], t[1]))
        container_rows.append({"kind": row["kind"], "count": row["count"], "bytes": row["bytes"],
                               "largest": [{"path": r, "size": s} for s, r in row["files"][:5]]})
    container_rows.sort(key=lambda r: (-r["bytes"], r["kind"]))
    largest = sorted(((s, r, kinds[r]["kind"] or ("." + _ext(r))) for k in cont.values() for s, r in k["files"]),
                     key=lambda t: (-t[0], t[1]))[:15]

    # entropy of the first bytes of the largest containers
    ent = []
    for size, rel, kind in largest:
        if len(ent) >= ENTROPY_FILES or size < (1 << 20):
            continue
        if meter.expired():
            break
        sample = meter.read(os.path.join(root, *rel.split("/")), 0, ENTROPY_BYTES)
        if len(sample) < ENTROPY_BYTES:
            continue
        e = entropy(sample)
        by_design = kind in ("zip", "gzip", "xz", "zstd", "7z", "rar", "bzip2")
        verdict = ("likely encrypted or compressed" if e >= ENTROPY_LIMIT else "structured or uncompressed header")
        if e >= ENTROPY_LIMIT and by_design:
            verdict += " (%s is compressed by design)" % kind
        ent.append({"path": rel, "size": size, "kind": kind, "entropy": round(e, 3), "verdict": verdict})

    # texture formats (sampled)
    fmt = {}
    for rel, d in details.items():
        label = d.get("format") or kinds[rel]["kind"]
        row = fmt.setdefault(label, {"format": label, "count_sampled": 0, "samples": []})
        row["count_sampled"] += 1
        if len(row["samples"]) < 3:
            row["samples"].append(rel)
    mk = {}
    for info in kinds.values():
        if info["kind"]:
            mk[info["kind"]] = mk.get(info["kind"], 0) + 1
    texture_rows = sorted(fmt.values(), key=lambda r: (-r["count_sampled"], r["format"]))

    # candidates (top N) with kind and size when known
    cands = []
    for c in cands_all:
        row = {"path": c["path"], "size": c["size"], "score": c["score"], "words": c["words"],
               "kind": kinds.get(c["path"], {}).get("kind") or ("ext:" + (_ext(c["path"]) or "none"))}
        d = details.get(c["path"], {})
        if d.get("w"):
            row["dims"] = "%dx%d" % (d["w"], d["h"])
        if d.get("format"):
            row["format"] = d["format"]
        cands.append(row)

    match = make_matcher(kws)
    names, not_listable = archive_names(ix, kinds, cont, match, meter, cap)
    hits_cfg = config_hits(ix, meter)
    engines = detect_engines(ix, kinds)
    # a CPK that lists .emb members is also evidence for the Dimps-style bundles
    for a in names:
        if a.get("listable") and a["format"] == "cpk" and dict(a.get("top_extensions", [])).get("emb"):
            for e in engines:
                if e["engine"] == "CRI CPK container":
                    e["evidence"].append("%s lists .emb bundles (Dimps style)" % a["archive"])

    top = {}
    for rel, size in files:
        head = rel.split("/")[0] if "/" in rel else rel
        row = top.setdefault(head, {"name": head, "type": "dir" if "/" in rel else "file", "files": 0, "bytes": 0})
        row["files"] += 1
        row["bytes"] += size
    top_level = sorted(top.values(), key=lambda r: (-r["bytes"], r["name"]))[:40]

    report = {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "install_dir_name": os.path.basename(os.path.abspath(given)),
        "limits": {"max_files": int(max_files), "time_budget_s": float(budget_s), "probe_kb": int(probe_kb),
                   "magic_bytes": MAGIC_BYTES, "header_bytes": HEADER_BYTES, "entropy_bytes": ENTROPY_BYTES,
                   "text_cap_bytes": TEXT_CAP},
        "truncated": {"files": bool(st["truncated_files"]), "time": bool(meter.timed_out),
                      "last_dir_walked": _clean(st["last_dir"])},
        "io": {"opens": meter.opens, "bytes_read": meter.bytes},
        "totals": {"files": len(files), "dirs": len(dirs), "bytes": total_bytes, "symlinks": st["link_count"],
                   "special": st["special"], "unreadable": st["unreadable"]},
        "symlinks_not_followed": [_clean(l) for l in links[:10]],
        "extensions": {"by_count": [[e, c, b] for e, c, b in by_count], "by_bytes": [[e, c, b] for e, c, b in by_bytes]},
        "top_level": top_level,
        "engines": engines,
        "runtimes": detect_runtimes(ix),
        "containers": {"kinds": container_rows,
                       "largest": [{"path": r, "size": s, "kind": k} for s, r, k in largest]},
        "magic_kinds": [{"kind": k, "count_sampled": v} for k, v in sorted(mk.items(), key=lambda kv: (-kv[1], kv[0]))],
        "textures": {"formats": texture_rows, "sampled": len(details),
                     "note": "formats come from a sample (<= %d per kind), not from every file" % DETAIL_PER_KIND},
        "entropy": ent,
        "candidates": cands,
        "candidate_total": len(ranked),
        "candidate_folders": candidate_folders(ranked),
        "archive_names": names,
        "archives_not_listable": not_listable,
        "variant_sets": variant_sets(ix),
        "input_layer": input_layer(ix),
        "config_hits": hits_cfg,
        "keywords": {"list": kws, "custom": custom},
        "notes": ["Triage only: every line is a lead (likely / possible / check), not a conclusion.",
                  "Saves, prefixes and anything outside the install folder are not read."],
    }
    if report["truncated"]["files"]:
        report["notes"].append("The walk stopped at %d entries (alphabetical order): later folders were not seen." % max_files)
    if report["truncated"]["time"]:
        report["notes"].append("The time budget of %ss ran out: the walk or a later phase is partial." % budget_s)
    report["strategy_hints"] = strategy_hints(report)
    report["seconds"] = round(clock() - started, 2)
    return report


# ---------------------------------------------------------------- the Deck side
def _vdf_value(text, key):
    m = re.search(r'"%s"\s+"([^"]*)"' % re.escape(key), text)
    return m.group(1) if m else ""


def remote_triage(req, steam, home=None, media_root=None):
    home = home or os.path.expanduser("~")
    if media_root is not None:
        steam.MEDIA_ROOT = media_root
    appid = str(req["appid"])
    app = steam.installed_apps(home).get(appid)
    if not app or not os.path.isdir(app["install_path"]):
        return {"schema": SCHEMA, "ok": False, "appid": appid,
                "error": "the game is not installed (no appmanifest_%s.acf in any library)" % appid}
    manifest = {}
    try:
        with open(app["manifest"], encoding="utf-8", errors="replace") as f:
            text = f.read(1 << 16)
        for k in ("name", "installdir", "buildid", "StateFlags", "TargetBuildID", "SizeOnDisk"):
            manifest[k] = _vdf_value(text, k)
    except OSError:
        pass
    rep = triage(app["install_path"], max_files=int(req.get("max_files", DEFAULT_MAX_FILES)),
                 budget_s=float(req.get("budget_s", DEFAULT_BUDGET_S)), keywords=req.get("keywords") or None,
                 probe_kb=int(req.get("probe_kb", DEFAULT_PROBE_KB)))
    rep.update(ok=True, mode="host", appid=appid, manifest=manifest)
    rep["prefix_present"] = any(os.path.isdir(os.path.join(lib, "steamapps", "compatdata", appid, "pfx"))
                                for lib in steam.library_paths(home))
    return rep


def remote_main(steam_source, request_json):
    """Entry point of the script sent to the Deck: prints one JSON object (errors included)."""
    steam = types.ModuleType("steam")
    try:
        exec(compile(steam_source, "steam.py", "exec"), steam.__dict__)
        env = os.environ
        result = remote_triage(json.loads(request_json), steam, home=env.get("TRIAGE_HOME") or None,
                               media_root=env.get("TRIAGE_MEDIA") or None)
    except Exception as exc:
        result = {"schema": SCHEMA, "ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    print(json.dumps(result, sort_keys=True))
# ===== REMOTE END =====

STEAM_PY = ROOT / "py_modules" / "button_glyphs" / "steam.py"
DECK_SH = ROOT.parent / ".claude" / "scripts" / "deck.sh"


def remote_source():
    """The text between the REMOTE markers of this very file."""
    text = Path(__file__).read_text(encoding="utf-8")
    start = text.index("# ===== REMOTE BEGIN")
    start = text.index("\n", start) + 1
    return text[start:text.index("# ===== REMOTE END")]


def build_request(appid, max_files, budget_s, keywords, probe_kb):
    return {"appid": str(appid), "max_files": max_files, "budget_s": budget_s, "keywords": keywords or [],
            "probe_kb": probe_kb}


def build_script(request, steam_source=None, remote=None):
    """The program sent on stdin: the remote part, then one call with the plugin's steam.py and the request."""
    steam_source = steam_source if steam_source is not None else STEAM_PY.read_text(encoding="utf-8")
    remote = remote if remote is not None else remote_source()
    return "%s\nremote_main(%r, %r)\n" % (remote, steam_source, json.dumps(request, sort_keys=True))


def run_remote(script, budget_s, deck_sh=None):
    """Send `script` on stdin of `deck.sh 'python3 -'`. The command text names no game path."""
    deck_sh = str(deck_sh or os.environ.get("DECK_SH") or DECK_SH)
    timeout = float(budget_s) + 180
    # a first, stdin-less call opens (or reuses) the SSH master so the real call cannot lose its stdin to a login
    subprocess.run([deck_sh, "true"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    return subprocess.run([deck_sh, "python3 -"], input=script, capture_output=True, text=True, timeout=timeout)


def check_reachable(deck_sh=None):
    deck_sh = str(deck_sh or os.environ.get("DECK_SH") or DECK_SH)
    p = subprocess.run([deck_sh, "--check"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def parse_result(stdout):
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("the Deck answered without a JSON object")


def _cut(text, n):
    """text shortened at a word boundary."""
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0].rstrip(",;:") + "..."


def _mb(n):
    return "%.1f GB" % (n / 1e9) if n >= 1e9 else "%.1f MB" % (n / 1e6) if n >= 1e6 else "%.1f KB" % (n / 1e3)


def summarize(rep, limit=25):
    """At most `limit` short lines for a human (and for the recon agent's first read)."""
    L = []
    tr = rep["truncated"]
    head = "Triage of %s" % rep.get("install_dir_name", "?")
    if rep.get("manifest"):
        m = rep["manifest"]
        head += " (appid %s, build %s, state %s)" % (rep.get("appid"), m.get("buildid"), m.get("StateFlags"))
    t = rep["totals"]
    head += ": %d files, %d dirs, %s" % (t["files"], t["dirs"], _mb(t["bytes"]))
    if tr["files"] or tr["time"]:
        head += "  [TRUNCATED: %s]" % " + ".join(k for k in ("files", "time") if tr[k])
    L.append(head)
    eng = [e for e in rep["engines"] if e["engine"] != "unknown"][:3]
    if eng:
        e0 = eng[0]
        L.append("Engine: %s (%s, score %d): %s" % (e0["engine"], e0["confidence"], e0["score"], "; ".join(e0["evidence"][:3])))
        if len(eng) > 1:
            L.append("Also: " + "; ".join("%s (%s)" % (e["engine"], e["confidence"]) for e in eng[1:]))
    else:
        L.append("Engine: unknown (no signature matched)")
    if rep["runtimes"]:
        L.append("Runtime: " + "; ".join(r["name"] for r in rep["runtimes"][:3]))
    c = rep["containers"]["kinds"]
    L.append("Containers: " + (", ".join("%s x%d (%s)" % (k["kind"], k["count"], _mb(k["bytes"])) for k in c[:5]) or "none seen"))
    tx = rep["textures"]["formats"]
    L.append("Textures (sampled): " + (", ".join("%s x%d" % (f["format"], f["count_sampled"]) for f in tx[:5]) or "none seen"))
    for e in rep["entropy"][:2]:
        L.append("Entropy %s: %.2f bits/byte, %s" % (e["path"], e["entropy"], e["verdict"]))
    L.append("Extensions by count: " + ", ".join("%s %d" % (e, n) for e, n, _ in rep["extensions"]["by_count"][:8]))
    L.append("Extensions by bytes: " + ", ".join("%s %s" % (e, _mb(b)) for e, _, b in rep["extensions"]["by_bytes"][:6]))
    vs = rep["variant_sets"]
    L.append("Variant sets by name: %s; %d paired names (ps+xbox: %d)" % (
        ", ".join("%s x%d in %s" % (k, v, (vs["folders"].get(k) or ["?"])[0]) for k, v in sorted(vs["files_by_family"].items()))
        or "none", vs["paired_count"], vs["ps_and_xbox_pairs"]))
    if rep["input_layer"]:
        L.append("Input layer files: " + ", ".join(rep["input_layer"][:5]))
    cs = rep["candidates"]
    L.append("Candidate folders (%d files match): " % rep["candidate_total"] +
             ", ".join("%s x%d [%d]" % (f["folder"], f["count"], f["best_score"]) for f in rep["candidate_folders"][:6]))
    L.append("Top candidates (of %d listed in the JSON): " % len(cs) +
             ", ".join("%s%s" % (c["path"], " " + c["dims"] if c.get("dims") else "") for c in cs[:4]))
    for a in rep["archive_names"][:3]:
        if a["listable"]:
            L.append("Names in %s (%s): %d entries, %d match: %s" % (
                a["archive"], a["format"], a["entries"], a["matching"], ", ".join(n["name"] for n in a["names"][:4])))
        else:
            L.append("Archive %s (%s): not listable (%s)" % (a["archive"], a["format"], a.get("note", "")))
    if rep["archives_not_listable"]:
        L.append("Not listable here: " + ", ".join("%s x%d" % (n["format"], n["count"]) for n in rep["archives_not_listable"][:5]))
    for h in rep["strategy_hints"][:5]:
        L.append("Hint [%s] %s: %s | check: %s" % (h["likelihood"], h["strategy"].split(" (")[0], _cut(h["reason"], 100),
                                                  _cut(h["check_next"], 100)))
    L = [_clean(l) if len(l) <= 220 else _clean(l[:217]) + "..." for l in L]
    if len(L) > limit:
        L = L[:limit - 1] + ["... (%d more lines in the JSON)" % (len(L) - limit + 1)]
    return "\n".join(L)


class _Parser(argparse.ArgumentParser):
    def error(self, message):                # exit 2 is reserved for "Deck unreachable"
        self.print_usage(sys.stderr)
        print("%s: error: %s" % (self.prog, message), file=sys.stderr)
        sys.exit(64)


def main(argv=None):
    ap = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="the install folder (local mode)")
    ap.add_argument("--appid", help="host mode: find the install through the Deck")
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES, help="walk at most this many entries")
    ap.add_argument("--keywords", help="comma separated words that replace the default prompt keywords")
    ap.add_argument("--time-budget", type=float, default=DEFAULT_BUDGET_S, help="seconds before it reports 'truncated'")
    ap.add_argument("--probe-kb", type=int, default=DEFAULT_PROBE_KB, help="most KB read from one archive index")
    ap.add_argument("--dry-run-request", action="store_true", help="host mode: print the request and exit")
    ns = ap.parse_args(argv)
    if bool(ns.path) == bool(ns.appid):
        ap.error("give exactly one of <install dir> or --appid <id>")
    keywords = [k for k in (ns.keywords or "").split(",") if k.strip()]
    if ns.appid:
        req = build_request(ns.appid, ns.max_files, ns.time_budget, keywords, ns.probe_kb)
        if ns.dry_run_request:
            print(json.dumps(req, indent=2, sort_keys=True))
            return 0
        reachable, why = check_reachable()
        if not reachable:
            print("Deck unreachable: %s\nWake it up and run .claude/scripts/deck.sh --check; nothing was done." % why,
                  file=sys.stderr)
            return 2
        done = run_remote(build_script(req), ns.time_budget)
        if done.returncode != 0 and not done.stdout.strip():
            print("remote run failed (exit %d): %s" % (done.returncode, done.stderr.strip()[-300:]), file=sys.stderr)
            return 2
        try:
            rep = parse_result(done.stdout)
        except ValueError as exc:
            print("%s: %s" % (exc, done.stdout.strip()[-200:]), file=sys.stderr)
            return 1
        if not rep.get("ok"):
            print("triage failed: %s" % rep.get("error"), file=sys.stderr)
            return 1
    else:
        if not os.path.isdir(ns.path):
            print("not a folder: %s" % ns.path, file=sys.stderr)
            return 1
        rep = triage(ns.path, max_files=ns.max_files, budget_s=ns.time_budget, keywords=keywords or None,
                     probe_kb=ns.probe_kb)
        rep.update(ok=True, mode="local")
    print(summarize(rep))
    if ns.json:
        with open(ns.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=2, sort_keys=True)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
