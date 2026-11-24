"""Loose XNB textures replaced as whole pictures (files-level strategy, see files_patch.py).

Some games keep every prompt in a tiny file of its own (Songs for a Hero: `Content/Tooltip/A.xnb`,
28x23 RGBA, 2661 bytes). The files cannot carry a marker, so the engine reads their state from
SHA-256 (original / patched / stale / unknown); this profile only says what a file looks like and
how to draw it.

An entry (`files`) describes one file:
  key        name used in messages and for the backup file ("Tooltip/A")
  file       path relative to the install dir ("Content/Tooltip/A.xnb")
  size       size of the whole original file; width, height  of the texture
  header     bytes before the pixel block (the XNB header, never changed)
  sha256     SHA-256 of the whole original file (the build this profile knows)
  image      the bundled drawing, relative to masks/ without ".png" ("sfh/Tooltip_A")

The drawing is exactly the texture's size (RGBA, straight alpha, transparent pixels (0,0,0,0)), so
rendering is the original file with its pixel block replaced wholesale by the drawing's pixels:
the header is untouched and the length is the same.
"""
import os

from .. import files_patch, xnb
from .region_glyphs import PictureLoader

TEXTURE_BYTES = 4


class XnbPictureProfile:
    kind = "loose-files"
    files_level = True

    def __init__(self, appid, name, content_dir, files, roles, native_style, notes="",
                 revision=1, original_hint="", support="partial", styles=("ps",), pictures=None):
        self.appid = appid
        self.name = name
        self.file = content_dir           # the folder the files live in, relative to the install dir
        self.files = files
        self.roles = roles
        self.native_style = native_style  # the style the game already shows ("xbox")
        self.revision = revision
        self.support = support        # "full" (confirmed in game) or "partial": shown in the UI
        self.notes = notes
        self.original_hint = original_hint
        self.patch_styles = tuple(styles)
        self.styles = {}                  # no bundled icon sets for the UI check
        self.textures = []
        self.pictures = pictures or PictureLoader()
        self._validate()

    def _validate(self):
        """The table is code, not user input: a mistake in it fails at import, not in a game folder."""
        keys = set()
        paths = set()
        names = set()
        for e in self.files:
            key = e["key"]
            parts = e["file"].split("/")
            if key in keys or os.path.isabs(e["file"]) or ".." in parts or "" in parts:
                raise ValueError("%s: duplicate key or unsafe path %r" % (key, e["file"]))
            if e["file"] in paths:
                raise ValueError("%s: file %r is listed twice" % (key, e["file"]))
            keys.add(key)
            paths.add(e["file"])
            names.add(key.replace("/", "_"))
            pixels = e["width"] * e["height"] * TEXTURE_BYTES
            if e["size"] != e["header"] + pixels or len(e["sha256"]) != 64:
                raise ValueError("%s: size, header and texture size do not agree" % key)
            if e["size"] > files_patch.MAX_FILE:
                raise ValueError("%s: %d bytes is too large for the files strategy" % (key, e["size"]))
        if len(names) != len(keys):
            raise ValueError("two files would share one backup name")

    # ------------------------------------------------------------------ preflight

    def preflight(self):
        """Validate every bundled drawing BEFORE the first byte is written.

        Loads each picture (the loader caches it, so render() reuses the result) and checks that it
        has exactly its texture's size. Raises RuntimeError with a clear message, so a missing or
        damaged PNG fails the whole apply instead of stopping it halfway.
        """
        for e in self.files:
            try:
                rgba, w, h = self.pictures(e["image"])
                if (w, h) != (e["width"], e["height"]) or len(rgba) != w * h * TEXTURE_BYTES:
                    raise ValueError("it is %dx%d, the texture is %dx%d" % (w, h, e["width"], e["height"]))
            except Exception as exc:
                raise RuntimeError("%s: bundled picture %r is missing or damaged (%s)"
                                   % (e["key"], e["image"], exc))

    # ------------------------------------------------------------------ rendering

    def render(self, original, entry, style):
        """The file for `style`, from the original file's bytes: same length, same header."""
        if style not in self.patch_styles:
            raise ValueError("unknown style %r" % style)
        if len(original) != entry["size"]:
            raise ValueError("%s: wrong size" % entry["key"])
        tex = xnb.parse(original)
        if (tex.width, tex.height, tex.data_offset) != (entry["width"], entry["height"], entry["header"]):
            raise ValueError("%s: not the texture this profile knows" % entry["key"])
        rgba, w, h = self.pictures(entry["image"])
        if (w, h) != (entry["width"], entry["height"]) or len(rgba) != tex.pixel_size:
            raise ValueError("%s: picture %r does not have the texture's size" % (entry["key"], entry["image"]))
        return xnb.with_pixels(original, rgba)
