#!/usr/bin/env python3
"""Dev tool: how much of a candidate picture is pixel-identical to the game's original texture?

Candidate art for a profile must not carry the game's own pixels (invariant 6). This tool measures it
for one picture or a folder of them, against the original MonoGame .xnb textures. Needs Pillow + numpy
(dev only). Nothing is written; it prints a table and always exits 0 (the judgement is a human's).

    python3 tools/art_overlap.py <candidate.png | folder> <original.xnb | Content-dir>

  * one PNG + one .xnb: a single comparison.
  * a folder of PNGs + a Content folder: every PNG is matched to the .xnb whose path under Content,
    without ".xnb" and with "/" written as "_", equals the PNG's name (masks/sfh/Tooltip_A.png <->
    Content/Tooltip/A.xnb).

Per picture (both textures must have the same size, otherwise the row says so):
  opaque   pixels of the candidate with alpha >= 128
  both     of those, where the original is opaque too (the art overlaps there)
  same     of those, where the original pixel has exactly the same R,G,B ("equal pixel at the same position
           and colour"); share = same / opaque
  near     same but each channel within TOL (8): a recolour/resample that is nearly identical
  blocks   aligned 2x2 tiles where either picture has ink: identical (all four pixels equal, transparent
           pixels compared as transparent) / such tiles; share = identical / tiles
  colours  the most frequent colours among the identical pixels (a shared palette colour such as the
           D-pad highlight orange shows up here)
"""
import os
import sys

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "py_modules"))
from button_glyphs import xnb  # noqa: E402

ALPHA_ON = 128
TOL = 8


def read_xnb(path):
    """(h,w,4) uint8 pixels of a texture .xnb."""
    with open(path, "rb") as f:
        data = f.read()
    tex = xnb.parse(data)
    return np.frombuffer(xnb.pixels(data), dtype=np.uint8).reshape(tex.height, tex.width, 4)


def read_png(path):
    return np.array(Image.open(path).convert("RGBA"))


def compare(cand, orig):
    """Dict of overlap numbers between two (h,w,4) pictures of the same size."""
    c_on = cand[..., 3] >= ALPHA_ON
    o_on = orig[..., 3] >= ALPHA_ON
    both = c_on & o_on
    same = both & (cand[..., :3] == orig[..., :3]).all(axis=2)
    near = both & (np.abs(cand[..., :3].astype(int) - orig[..., :3].astype(int)) <= TOL).all(axis=2)
    # aligned 2x2 tiles; transparent pixels are compared as transparent whatever their colour
    h, w = cand.shape[0] // 2 * 2, cand.shape[1] // 2 * 2
    c = np.where(c_on[..., None], cand, 0)[:h, :w]
    o = np.where(o_on[..., None], orig, 0)[:h, :w]

    def tiles(a):
        return a.reshape(h // 2, 2, w // 2, 2, 4).transpose(0, 2, 1, 3, 4).reshape(h // 2, w // 2, 16)

    tc, to = tiles(c), tiles(o)
    ink = (tc.any(axis=2)) | (to.any(axis=2))
    ident = ink & (tc == to).all(axis=2)
    colours = {}
    for px in cand[same][:, :3].tolist():
        key = "#%02x%02x%02x" % tuple(px)
        colours[key] = colours.get(key, 0) + 1
    top = sorted(colours.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    return {"opaque": int(c_on.sum()), "both": int(both.sum()), "same": int(same.sum()),
            "near": int(near.sum()), "tiles": int(ink.sum()), "identical_tiles": int(ident.sum()),
            "colours": top}


def pct(n, d):
    return "%5.1f%%" % (100.0 * n / d) if d else "   n/a"


def content_index(root):
    """{png-style name: path} for every .xnb under root ('Menu/xbox/a.xnb' -> 'Menu_xbox_a')."""
    index, clashes = {}, set()
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(".xnb"):
                rel = os.path.relpath(os.path.join(base, name), root)[:-4].replace(os.sep, "/")
                key = rel.replace("/", "_")
                if key in index:
                    clashes.add(key)
                index[key] = os.path.join(base, name)
    for key in clashes:
        index.pop(key, None)
    return index


def pairs(candidate, original):
    """[(label, candidate png, original xnb or None)] from the two command-line arguments."""
    if os.path.isdir(candidate):
        if not os.path.isdir(original):
            raise SystemExit("a folder of candidates needs the original Content folder")
        index = content_index(original)
        return [(n[:-4], os.path.join(candidate, n), index.get(n[:-4]))
                for n in sorted(os.listdir(candidate)) if n.lower().endswith(".png")]
    return [(os.path.basename(candidate)[:-4], candidate, original)]


def report(rows):
    head = "%-24s %5s %5s %8s %8s %8s %8s %12s %8s  %s" % (
        "picture", "size", "opaq", "both", "same", "share", "near", "2x2 same", "share", "top identical colours")
    lines = [head, "-" * len(head)]
    totals = [0, 0, 0, 0, 0]
    for label, cand_path, orig_path in rows:
        try:
            if orig_path is None:
                lines.append("%-24s no matching .xnb found" % label)
                continue
            cand, orig = read_png(cand_path), read_xnb(orig_path)
            if cand.shape != orig.shape:
                lines.append("%-24s size differs: candidate %dx%d, original %dx%d" % (
                    label, cand.shape[1], cand.shape[0], orig.shape[1], orig.shape[0]))
                continue
            r = compare(cand, orig)
        except (OSError, ValueError, xnb.XnbError) as exc:
            lines.append("%-24s error: %s" % (label, exc))
            continue
        totals = [totals[0] + r["opaque"], totals[1] + r["same"], totals[2] + r["near"],
                  totals[3] + r["tiles"], totals[4] + r["identical_tiles"]]
        lines.append("%-24s %2dx%-2d %5d %8d %8d %8s %8d %5d/%-5d %8s  %s" % (
            label, cand.shape[1], cand.shape[0], r["opaque"], r["both"], r["same"], pct(r["same"], r["opaque"]),
            r["near"], r["identical_tiles"], r["tiles"], pct(r["identical_tiles"], r["tiles"]),
            " ".join("%s x%d" % kv for kv in r["colours"]) or "-"))
    lines.append("-" * len(head))
    lines.append("%-24s %5s %5d %8s %8d %8s %8d %5d/%-5d %8s" % (
        "TOTAL", "", totals[0], "", totals[1], pct(totals[1], totals[0]), totals[2], totals[4], totals[3],
        pct(totals[4], totals[3])))
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    print(report(pairs(argv[0], argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
