#!/usr/bin/env python3
"""Split a flattened Figma SVG export (an icon sheet) into one SVG file per icon.

Every top-level drawable is assigned to an icon cell; each output keeps only the
elements of that cell plus the <mask>/<clipPath>/<filter>/defs they reference,
with a viewBox cropped to the icon.

Usage:
  split_svg_sheet.py grid  <sheet.svg> <out_dir> <names.txt>
      cells are found by clustering element centres into rows/columns;
      names.txt has one line per row, names separated by spaces, '-' = skip.
  split_svg_sheet.py rects <sheet.svg> <out_dir> <rects.txt>
      rects.txt lines: "<name> x y w h" (icon frames in sheet coordinates);
      each element goes to the frame whose centre is nearest.
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SVG = "http://www.w3.org/2000/svg"
XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG)
ET.register_namespace("xlink", XLINK)
HREF = "{%s}href" % XLINK
NUM = re.compile(r"-?\d*\.?\d+(?:e-?\d+)?")
REF = re.compile(r"url\(#([^)]+)\)")


def tag(el):
    return el.tag.split("}")[-1]


def path_bbox(d):
    xs, ys = [], []
    for cmd, args in re.findall(r"([MLHVCSQTAZmlhvcsqtaz])([^MLHVCSQTAZmlhvcsqtaz]*)", d):
        nums = [float(n) for n in NUM.findall(args)]
        if cmd in "MLCSQT":
            xs += nums[0::2]
            ys += nums[1::2]
        elif cmd == "H":
            xs += nums
        elif cmd == "V":
            ys += nums
        elif cmd == "A":
            for i in range(0, len(nums), 7):
                xs.append(nums[i + 5])
                ys.append(nums[i + 6])
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def union(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


class Sheet:
    def __init__(self, path):
        self.tree = ET.parse(path)
        self.root = self.tree.getroot()
        self.byid = {}
        self.owner = {}  # id -> top-level element that contains it
        for top in self.root:
            for el in top.iter():
                i = el.get("id")
                if i:
                    self.byid[i] = el
                    self.owner[i] = top
        w = float(self.root.get("width"))
        h = float(self.root.get("height"))
        self.items = []
        for top in self.root:
            t = tag(top)
            if t in ("mask", "defs", "clipPath", "filter"):
                continue
            if t == "rect" and abs(float(top.get("width", 0)) - w) < 1 and abs(float(top.get("height", 0)) - h) < 1:
                continue  # page background
            bb = self.bbox(top)
            if bb:
                self.items.append((top, bb))

    def bbox(self, el):
        t = tag(el)
        if t == "path":
            bb = path_bbox(el.get("d", ""))
        elif t == "circle":
            cx, cy, r = (float(el.get(k, 0)) for k in ("cx", "cy", "r"))
            bb = [cx - r, cy - r, cx + r, cy + r]
        elif t == "ellipse":
            cx, cy, rx, ry = (float(el.get(k, 0)) for k in ("cx", "cy", "rx", "ry"))
            bb = [cx - rx, cy - ry, cx + rx, cy + ry]
        elif t == "rect":
            if el.get("transform"):
                return None
            x, y, w, h = (float(el.get(k, 0)) for k in ("x", "y", "width", "height"))
            bb = [x, y, x + w, y + h]
        elif t == "use":
            ref = (el.get(HREF) or el.get("href") or "").lstrip("#")
            return self.bbox(self.byid[ref]) if ref in self.byid else None
        elif t == "g":
            bb = None
            for c in el:
                if tag(c) not in ("mask", "clipPath", "defs"):
                    bb = union(bb, self.bbox(c))
            return bb
        else:
            return None
        sw = float(el.get("stroke-width", 1 if el.get("stroke") else 0))
        if bb and sw:
            bb = [bb[0] - sw / 2, bb[1] - sw / 2, bb[2] + sw / 2, bb[3] + sw / 2]
        return bb

    def deps(self, elements):
        """Top-level elements / defs children referenced (transitively) by `elements`."""
        need_top, need_defs, seen = [], [], set()
        stack = list(elements)
        while stack:
            el = stack.pop()
            text = ET.tostring(el, encoding="unicode")
            ids = REF.findall(text) + [h.lstrip("#") for h in re.findall(r'href="(#[^"]+)"', text)]
            for i in ids:
                if i in seen or i not in self.byid:
                    continue
                seen.add(i)
                top = self.owner[i]
                if tag(top) == "defs":
                    target = self.byid[i]
                    # the direct child of <defs> holding this id
                    for c in top:
                        if c is target or target in list(c.iter()):
                            target = c
                            break
                    if target not in need_defs:
                        need_defs.append(target)
                        stack.append(target)
                elif top not in need_top and top not in elements:
                    need_top.append(top)
                    stack.append(top)
        return need_top, need_defs

    def write(self, name, elements, out_dir, pad=1.0):
        bb = None
        for el in elements:
            bb = union(bb, self.bbox(el))
        x0, y0, x1, y1 = bb[0] - pad, bb[1] - pad, bb[2] + pad, bb[3] + pad
        w, h = x1 - x0, y1 - y0
        side = max(w, h)  # square canvas, icon centred
        x0 -= (side - w) / 2
        y0 -= (side - h) / 2
        need_top, need_defs = self.deps(elements)
        order = {id(e): k for k, e in enumerate(self.root)}
        body = sorted(set(need_top) | set(elements), key=lambda e: order[id(e)])
        svg = ET.Element("{%s}svg" % SVG, {
            "width": "%g" % round(side, 3), "height": "%g" % round(side, 3),
            "viewBox": "%g %g %g %g" % (round(x0, 3), round(y0, 3), round(side, 3), round(side, 3)),
            "fill": "none"})
        for e in body:
            svg.append(e)
        if need_defs:
            d = ET.SubElement(svg, "{%s}defs" % SVG)
            for e in need_defs:
                d.append(e)
        text = ET.tostring(svg, encoding="unicode")
        if "xlink:" not in text:
            text = text.replace(' xmlns:xlink="%s"' % XLINK, "")
        Path(out_dir, name + ".svg").write_text(text + "\n")
        return side


def cluster(values, gap):
    vals = sorted(values)
    groups = [[vals[0]]]
    for v in vals[1:]:
        if v - groups[-1][-1] > gap:
            groups.append([v])
        else:
            groups[-1].append(v)
    return [sum(g) / len(g) for g in groups]


def nearest(v, centres):
    return min(range(len(centres)), key=lambda i: abs(centres[i] - v))


def main():
    mode, src, out, spec = sys.argv[1:5]
    Path(out).mkdir(parents=True, exist_ok=True)
    sh = Sheet(src)
    cells = {}
    if mode == "grid":
        names = [l.split() for l in Path(spec).read_text().splitlines() if l.strip()]
        cy = lambda b: (b[1] + b[3]) / 2
        cx = lambda b: (b[0] + b[2]) / 2
        rows = cluster([cy(b) for _, b in sh.items], 18)
        cols = cluster([cx(b) for _, b in sh.items], 18)
        if len(rows) != len(names):
            sys.exit("found %d rows, names file has %d: %s" % (len(rows), len(names), rows))
        for el, b in sh.items:
            r, c = nearest(cy(b), rows), nearest(cx(b), cols)
            if c >= len(names[r]) or names[r][c] == "-":
                sys.exit("element at row %d col %d has no name (cols=%s)" % (r, c, cols))
            cells.setdefault(names[r][c], []).append(el)
    else:
        frames = []
        for l in Path(spec).read_text().splitlines():
            if l.strip():
                n, x, y, w, h = l.split()
                frames.append((n, float(x) + float(w) / 2, float(y) + float(h) / 2))
        for el, b in sh.items:
            mx, my = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
            n = min(frames, key=lambda f: (f[1] - mx) ** 2 + (f[2] - my) ** 2)[0]
            cells.setdefault(n, []).append(el)
    for name, els in sorted(cells.items()):
        side = sh.write(name, els, out)
        print("%-24s %2d elements  %.1fpx" % (name, len(els), side))
    print("total", len(cells))


if __name__ == "__main__":
    main()
