"""Games that already ship both button sets and pick one in code.

Some XNA/FNA ports draw PlayStation prompts when the pad's GUID is a DualShock 4 and
Xbox prompts for everything else. On the Steam Deck the pad always looks like an
Xbox 360 one, so the PlayStation art is never used. A profile of this kind changes the
constant the game stores in its "pad type" table, forcing one set for every controller.

The edit is found by a byte pattern (IL with wildcards for metadata tokens), so it does
not depend on file offsets and survives rebuilds that only move tokens around. The
pattern must match exactly once, otherwise the file is left alone.
"""
import re


class IlPatchProfile:
    kind = "il-patch"

    def __init__(self, appid, name, file, roles, pattern, edits, anchors=(), notes="",
                 revision=1, original_hint=""):
        self.appid = appid
        self.revision = revision      # bump when the output changes, so applied files get redone
        self.name = name
        self.file = file              # path relative to the game's install dir
        self.roles = roles            # [(role, label)] shown under the style preview
        self.pattern = re.compile(pattern, re.DOTALL)
        self.edits = edits            # {style: {offset in match: new byte}}
        self.anchors = anchors        # byte strings that must also be present in the file
        self.notes = notes
        self.original_hint = original_hint
        self.styles = {}              # no icon sets: the art is the game's own
        self.textures = []

    def _find(self, data):
        matches = list(self.pattern.finditer(data))
        if len(matches) != 1:
            return None, "found %d places to patch in %s, expected 1" % (len(matches), self.file)
        return matches[0], None

    def check(self, path):
        """Problems that make `path` unsafe to patch (empty list = OK). Expects an original file."""
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            return ["cannot read %s: %s" % (self.file, exc)]
        problems = ["%s lacks %r" % (self.file, a) for a in self.anchors if a not in data]
        _, err = self._find(data)
        if err:
            problems.append(err)
        return problems

    def patch(self, path, style, load_icon=None):
        """Apply `style` to the original file at `path` (modified in place, same size)."""
        if style not in self.edits:
            raise ValueError("unknown style %r" % style)
        with open(path, "rb") as f:
            data = bytearray(f.read())
        match, err = self._find(bytes(data))
        if err:
            raise ValueError(err)
        for offset, value in self.edits[style].items():
            data[match.start() + offset] = value
        with open(path, "r+b") as f:
            f.write(data)
            f.flush()
