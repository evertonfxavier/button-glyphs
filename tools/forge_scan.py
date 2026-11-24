"""Read-only scan of Scimitar .forge files: textures (size, format, button-coloured blocks) and
button-related strings, one JSON per forge.

    python3 tools/forge_scan.py <dir with .forge files> <out dir>

Decompressing gigabytes with the pure-Python LZO of the plugin is slow (~0.5 MB/s). For bulk
analysis install the native decoder in a venv and run this with that interpreter:

    python3 -m venv /tmp/venv_lzo && /tmp/venv_lzo/bin/pip install lzallright
    /tmp/venv_lzo/bin/python -I tools/forge_scan.py <forge dir> <out dir>

Output per forge (JSON): entries, textures [{entry, id, name, w, h, fmt, colours?}], strings
[{entry, type, resource, text}], resource type counts. Use it to find button textures by name
and by Xbox button colours (green/red/blue/yellow DXT endpoints). Already-scanned forges are
skipped, so it can be re-run after copying more files. Never point it at the Deck's live files
over the network: copy them to the Mac first.
"""
import json
import os
import re
import struct
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "py_modules"))
from button_glyphs import forge as F  # noqa: E402

try:  # optional native decoder, orders of magnitude faster than the stdlib one
    import lzallright

    _LZO = lzallright.LZOCompressor()
    F.lzo1x_decompress = lambda raw, usz=None: _LZO.decompress(raw, usz)
except ImportError:
    sys.stderr.write("lzallright not installed: using the slow pure-Python LZO decoder" + chr(10))

TEXTURE = 0xA2B7E917
FORMATS = {1: "DXT1", 2: "DXT1", 3: "DXT1", 4: "DXT3", 5: "DXT5", 0: "RGBA"}
KEYWORDS = re.compile(
    r"(?i)(button|trigger|bumper|gamepad|controller|joypad|joystick|d-?pad|xbox|playstation|"
    r"\bps3\b|x360|press |hold |\btap\b|\bLB\b|\bRB\b|\bLT\b|\bRT\b)")
ASCII = re.compile(rb"[\x20-\x7e]{6,}")
UTF16 = re.compile(rb"(?:[\x20-\x7e]\x00){6,}")

# Xbox 360 button colours in the game's glyph art (RGB), matched against DXT endpoints
TARGETS = {"green": (60, 179, 112), "red": (218, 62, 70), "blue": (46, 136, 170),
           "yellow": (255, 215, 0)}


def c565(c):
    r, g, b = (c >> 11) & 31, (c >> 5) & 63, c & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def near(a, b, tol=28):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def colour_hits(res, fmt, w, h, off):
    if fmt not in (4, 5) or w * h > 256 * 256:
        return {}
    data = res[off:off + w * h]
    hits = {k: 0 for k in TARGETS}
    for i in range(0, len(data) - 15, 16):
        c0, c1 = struct.unpack_from("<HH", data, i + 8)
        for end in (c565(c0), c565(c1)):
            for k, t in TARGETS.items():
                if near(end, t):
                    hits[k] += 1
    return {k: v for k, v in hits.items() if v}


def scan_forge(path):
    out = {"forge": os.path.basename(path), "entries": [], "textures": [], "strings": [],
           "types": {}, "errors": []}
    fg = F.Forge(path)
    for e in fg.entries:
        if e.size < 1000:
            continue
        try:
            df = fg.load(e.name)
            data = df.data_block.read(0, df.data_block.size)
        except Exception as exc:  # not a resource payload / unknown layout
            out["errors"].append("%s: %s" % (e.name or e.index, exc))
            continue
        out["entries"].append({"name": e.name, "index": e.index, "resources": len(df.resources),
                               "bytes": len(data)})
        for fid, (start, size) in df.resources.items():
            if size < 16:
                continue
            typ, _, nl = struct.unpack_from("<III", data, start)
            if nl > 200:
                continue
            name = data[start + 12:start + 12 + nl].decode("latin-1")
            out["types"][hex(typ)] = out["types"].get(hex(typ), 0) + 1
            res = data[start:start + size]
            if typ == TEXTURE:
                try:
                    p = 12 + nl + 1 + 8
                    w, h, _, fmt = struct.unpack_from("<4I", res, p)
                    mark = res.find(bytes.fromhex("e97f2313"), p)
                    off = mark + 8
                    rec = {"entry": e.name, "id": hex(fid), "name": name, "w": w, "h": h,
                           "fmt": FORMATS.get(fmt, str(fmt)), "bytes": size}
                    hits = colour_hits(res, fmt, w, h, off)
                    if hits:
                        rec["colours"] = hits
                    out["textures"].append(rec)
                except Exception as exc:
                    out["errors"].append("tex %s: %s" % (name, exc))
            elif size <= 2 * 1024 * 1024:
                found = set()
                for rx, dec in ((ASCII, "latin-1"), (UTF16, "utf-16le")):
                    for m in rx.finditer(res):
                        s = m.group().decode(dec, "replace")
                        if KEYWORDS.search(s):
                            found.add(s[:160])
                for s in sorted(found)[:12]:
                    out["strings"].append({"entry": e.name, "type": hex(typ), "resource": name or hex(fid),
                                           "text": s})
    return out


def main():
    forge_dir, out_dir = sys.argv[1:3]
    os.makedirs(out_dir, exist_ok=True)
    files = sorted(os.path.join(forge_dir, f) for f in os.listdir(forge_dir) if f.endswith(".forge"))
    done = {f[:-5] for f in os.listdir(out_dir) if f.endswith(".json")}
    todo = [f for f in files if os.path.basename(f)[:-6] not in done]
    with Pool(min(4, len(todo) or 1)) as pool:
        for res in pool.imap_unordered(scan_forge, todo):
            name = res["forge"][:-6]
            with open(os.path.join(out_dir, name + ".json"), "w") as f:
                json.dump(res, f)
            print("%-32s entries %2d textures %5d strings %4d errors %d" % (
                res["forge"], len(res["entries"]), len(res["textures"]), len(res["strings"]),
                len(res["errors"])), flush=True)


if __name__ == "__main__":
    main()
