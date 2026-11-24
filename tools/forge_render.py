"""Render named textures of a Scimitar .forge to PNG (for contact sheets and visual checks).

    python3 tools/forge_render.py <file.forge> <out dir> <texture name> [<texture name> ...]

Rows of TGA-sourced textures are stored bottom-up: the PNGs come out upside down, exactly as the
bytes are. Flip them (and put them on a dark background, upscaled nearest-neighbour) when
comparing with what the game shows. Faster with a native LZO decoder, see tools/forge_scan.py.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "py_modules"))
from button_glyphs import forge as F, image  # noqa: E402

try:
    import lzallright

    _L = lzallright.LZOCompressor()
    F.lzo1x_decompress = lambda raw, usz=None: _L.decompress(raw, usz)
except ImportError:
    pass
FM = {1:"DXT1",2:"DXT1",3:"DXT1",4:"DXT3",5:"DXT5"}
def find(path, wanted, out):
    fg = F.Forge(path); found = set()
    for e in fg.entries:
        if e.size < 1000 or len(found) == len(wanted): continue
        try: df = fg.load(e.name); data = df.data_block.read(0, df.data_block.size)
        except Exception: continue
        for fid,(s,sz) in df.resources.items():
            typ,_,nl = struct.unpack_from("<III", data, s)
            if typ != 0xA2B7E917 or nl > 200: continue
            name = data[s+12:s+12+nl].decode("latin-1")
            if name not in wanted or name in found: continue
            res = data[s:s+sz]; p = 12+nl+1+8
            w,h,_,fmt = struct.unpack_from("<4I", res, p); m = res.find(bytes.fromhex("e97f2313"), p)
            if fmt not in FM: continue
            rgba = image.decode(res[m+8:m+8+(w*h if FM[fmt]!="DXT1" else w*h//2)], w, h, FM[fmt])
            image.write_png(os.path.join(out, name + ".png"), rgba, w, h); found.add(name)
    return found
if __name__ == "__main__":
    path, out = sys.argv[1], sys.argv[2]; names = set(sys.argv[3:])
    print(os.path.basename(path), sorted(find(path, names, out)))
