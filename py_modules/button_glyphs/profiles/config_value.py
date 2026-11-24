"""Games that already ship both button sets and remember the choice in a save/config file.

Some games keep "which button set to draw" as a stored value (an INI key, a number) next
to the player's progress. A profile of this kind changes that one value, nothing else:
no art is drawn and no game binary is touched. The file is user data that the game
rewrites by itself, so the strategy differs from the other ones:

  * the state is read from the value in the file (the value *is* the marker);
  * the value found before our first change is recorded by the engine and restored on
    "Original" (the player may have had a non-default value);
  * the edit replaces the digits of the value in place (same length), never the file;
  * the layout is checked first (one section, one line, expected number format) and the
    game build is pinned (size + SHA-256 of a data file), because the numbers only mean
    something for the build they were taken from.

This module holds the knowledge (where the file is, how to find the value, which builds
are known); the engine (`Engine._sync_config`) owns backups, config and timing.
"""
import hashlib
import os
import re

MAX_FILE = 1 << 20           # a save bigger than this is not what we expect: refuse, never slurp
CHUNK = 1 << 20
CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class ConfigValueError(ValueError):
    """The file is not in the layout the profile knows; nothing may be written to it."""


class ConfigValueProfile:
    kind = "config-value"
    config_level = True

    def __init__(self, appid, name, save, section, key, values, value_pattern, builds, roles,
                 notes="", revision=1, original_hint="", support="partial"):
        self.appid = appid
        self.name = name
        self.revision = revision      # bump when the output changes, so applied games get redone
        self.support = support        # "full" (confirmed in game) or "partial": shown in the UI
        self.file = save              # relative to <library>/steamapps/compatdata/<appid>/pfx
        self.section = section        # INI section that holds the key
        self.key = key
        self.values = dict(values)    # {style: exact value text}
        self.value_pattern = value_pattern
        self.builds = dict(builds)    # {buildid: {file, size, sha256}} (relative to the install dir)
        self.roles = roles
        self.notes = notes
        self.original_hint = original_hint
        self.patch_styles = tuple(s for s in ("ps", "xbox") if s in self.values)
        self.styles = {}              # no icon sets: the art is the game's own
        self.textures = []
        self.native_style = None      # "Original" is whatever the player had, not a fixed style

        self._section_b = section.encode("ascii")
        key_b = re.escape(key.encode("ascii"))
        self._key_line = re.compile(rb"[ \t]*" + key_b + rb"[ \t]*=")
        self._value_line = re.compile(
            rb"[ \t]*" + key_b + rb'="(' + value_pattern.encode("ascii") + rb')"[ \t]*\r?\n?')
        for style, value in self.values.items():
            if not self.valid_value(value):
                raise ValueError("%s: value for %s does not match the pattern" % (name, style))
        if len({len(v) for v in self.values.values()}) > 1:
            raise ValueError("%s: every style value must have the same length" % name)

    # ------------------------------------------------------------------ values

    def valid_value(self, value):
        return (isinstance(value, str) and re.fullmatch(self.value_pattern, value) is not None
                and (not self.values or len(value) == len(next(iter(self.values.values())))))

    def style_of(self, value):
        """The plugin style that writes `value`, or None for any other value (the player's own)."""
        for style, wanted in self.values.items():
            if wanted == value:
                return style
        return None

    # ------------------------------------------------------------------ the file

    def find_saves(self, libraries):
        """Existing save files, one per distinct file, searching the prefix in EVERY library."""
        found, seen = [], set()
        for lib in libraries:
            path = os.path.join(lib, "steamapps", "compatdata", self.appid, "pfx", self.file)
            real = os.path.realpath(path)
            if os.path.isfile(path) and real not in seen:
                seen.add(real)
                found.append(path)
        return found

    def locate(self, data):
        """(offset, value) of the one `key="..."` line inside `[section]`, or ConfigValueError."""
        if len(data) > MAX_FILE:
            raise ConfigValueError("the save is %d bytes; expected a small text file" % len(data))
        end = data.find(b"\0")
        text = data if end < 0 else data[:end]
        if end >= 0 and data[end:].strip(b"\0"):
            raise ConfigValueError("the save has data after its text; not a plain INI file")
        if CONTROL.search(text):
            raise ConfigValueError("the save is not plain text")
        sections = anywhere = 0
        current = None
        hits = []
        pos = 0
        for line in text.splitlines(keepends=True):
            start, pos = pos, pos + len(line)
            stripped = line.strip()
            if stripped.startswith(b"[") and stripped.endswith(b"]"):
                current = stripped[1:-1]
                if current == self._section_b:
                    sections += 1
                continue
            if self._key_line.match(line):
                anywhere += 1
                m = self._value_line.fullmatch(line)
                if current == self._section_b and m:
                    hits.append((start + m.start(1), m.group(1).decode("ascii")))
        if sections != 1:
            raise ConfigValueError("section [%s] found %d times, expected 1" % (self.section, sections))
        if anywhere != 1:
            raise ConfigValueError("found %d '%s' lines in the save, expected 1" % (anywhere, self.key))
        if len(hits) != 1:
            raise ConfigValueError("the '%s' line is not in [%s] or has an unexpected format"
                                   % (self.key, self.section))
        return hits[0]

    # ------------------------------------------------------------------ the game build

    @staticmethod
    def _hash(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
        return h.hexdigest()

    def build_stamp(self, buildid, install_path):
        """[size, mtime_ns] of the pinned file, or None. A cached "hash verified" is valid only
        while this stamp is unchanged, so a replaced data.win is hashed again."""
        pin = self.builds.get(buildid)
        if pin is None:
            return None
        try:
            st = os.stat(os.path.join(install_path, pin["file"]))
        except OSError:
            return None
        return [st.st_size, st.st_mtime_ns]

    def check_build(self, buildid, install_path, full=True):
        """Problems with the installed build (empty list = it is the pinned one).

        `full=False` checks only the build id and the size of the pinned file (cheap, used by
        the status); `full=True` also hashes it (done once per build id by the engine).
        """
        pin = self.builds.get(buildid)
        if pin is None:
            return ["build %s is not known (known: %s)" % (buildid or "?", ", ".join(self.builds))]
        path = os.path.join(install_path, pin["file"])
        try:
            size = os.path.getsize(path)
        except OSError as exc:
            return ["cannot read %s: %s" % (pin["file"], exc)]
        if size != pin["size"]:
            return ["%s has %d bytes, expected %d" % (pin["file"], size, pin["size"])]
        if full:
            try:
                if self._hash(path) != pin["sha256"]:
                    return ["%s does not match the checksum of build %s" % (pin["file"], buildid)]
            except OSError as exc:
                return ["cannot read %s: %s" % (pin["file"], exc)]
        return []
