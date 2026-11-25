"""Serious Sam HD: The First / The Second Encounter (Croteam, Serious Engine 3.5): the shared part of the two profiles.

Both games keep their prompts as HD `.tex` textures inside the root `.gro` archives of `Content/SeriousSamHD` /
`Content/SeriousSamHD_TSE` (plain ZIPs; the game folder's archives are SIGNED, see below):
  * `Textures/Menu/A_button..Y_button.tex`   32x32 RGBA8, 6 mips: glossy coloured ovals (HUD / menu hints)
  * `Textures/Menu/XBoxButton*.tex` and friends   64x64 DXT5, 7 mips: white glyphs on transparent (a disc with a cut-out
    letter, START / BACK pills, shoulder and trigger shapes, stick-click shapes)
  * `Textures/GlobalAtlas.rsc`  a CStaticAtlas: baked copies of the 64x64 prompts in the first DXT5 block (FE 256x128, TSE
    512x512), which is what the HUD actually draws; redrawn cell by cell with the block-granular writer
  * `Textures/Menu/GamepadPerspective.tex`  the 512x512 DXT5 pad drawing of the controls screen
and are replaced by an ADDED archive `zz_ButtonGlyphs.gro` (overlay strategy, overlay_patch.py): nothing of the game is
written, Original deletes the file. Per game the module in `serious_sam_hd_tfe.py` / `serious_sam_hd_tse.py` only holds the
registry constants (`VEHICLE`, revision) and calls `make_profile`.

SIGNED ARCHIVES (the uncertainty this profile is built around): the game folder holds a `SignatureCatalog.dat` and the
restricted exe (`SamHD*.exe`) prints, for an archive it cannot verify, "SKIPPED - This archive can only be used with
moddable (unrestricted) applications" (its log line is `<archive>: N files, signature: ...` or `... SKIPPED ...`), and
"Block signature verification failed". An unsigned extra `.gro` may therefore be ignored, and whether a LOOSE file
overrides an archive entry is unknown too. The first approved install is the test: read `Log/SamHD*.log` (a `signature:`
line for `zz_ButtonGlyphs.gro` = loaded; a `SKIPPED` line = ignored) and look at the buttons. The vehicle is ONE constant:
set `VEHICLE = "loose"` in the game's module (`loose` writes the same entries under the game folder, journaled and removed
again); both are implemented and tested.

Placement (same rules as Serious Sam 2, measured on the real textures with alpha > 24): each oval / disc is replaced by a
ROUND symbol centred on the original's box, its diameter the box's short side; shoulders, triggers, START, BACK and the
stick-click shapes are fitted into the whole box with their aspect kept. Under alpha 0 the originals store the colour of the
nearest visible pixel (a white disc bleeds white, a green oval green): ours bleeds its own colour the same way.
Faces use the outline coloured symbols (owner's choice); shoulders / triggers the SOLID `plain-rectangle-*` variants (the
outline ones vanish on light backgrounds); L3 / R3 the solid `plain-L3` / `plain-R3` discs; START `plain-big-option`;
BACK has only one PlayStation vector in the catalog (`outline-share`, thin).

The controls screen: `GamepadPerspective.tex` becomes the owner's DualShock 3 picture. The pad screen's arrows (Controller*
Arrow, Gamepad Strafe/Look overlays) are placed by the exe, no layout is readable, and the DualShock's left stick and
D-pad sit where the Xbox 360 pad has them the other way round: the arrows are EXPECTED to be misplaced on the DS3 (left
untouched; fixing it needs an exe patch, a later step).
"""
from . import serious_tables
from .overlay_glyphs import AtlasCells, OverlayGlyphProfile, RoundSymbol, WholePicture

ARCHIVE = "zz_ButtonGlyphs.gro"

ROLE_ART = {
    "a": "glyphs/ps/outline-blue-cross", "b": "glyphs/ps/outline-red-circle",
    "x": "glyphs/ps/outline-purple-square", "y": "glyphs/ps/outline-green-triangle",
    "lb": "glyphs/ps/plain-rectangle-L1", "rb": "glyphs/ps/plain-rectangle-R1",
    "lt": "glyphs/ps/plain-rectangle-L2", "rt": "glyphs/ps/plain-rectangle-R2",
    "l3": "glyphs/ps/plain-L3", "r3": "glyphs/ps/plain-R3",
    "start": "glyphs/ps/plain-big-option", "select": "glyphs/ps/outline-share",
}
ROUND_ROLES = ("a", "b", "x", "y")

# Opaque boxes (alpha > 24; x0, y0, x1, y1) measured on the real textures by a dev script (the 64x64 ones on the cells of
# the real atlas, which hold the same art as the standalone files: mean difference 0.00, max 8 = DXT noise).
SPECS = {
    "41000": {
        "title": "Serious Sam HD: The First Encounter",
        "pad_art": "masks/serious/ds3-hd-fe-512",
        "pad_clear": (0, 0, 0),
        "oval": {"a": (5, 4, 25, 28), "b": (7, 4, 27, 28), "x": (7, 4, 27, 28), "y": (5, 4, 25, 28)},   # 20 x 24
        "box": {r: (14, 14, 50, 50) for r in ROUND_ROLES},                                               # 36 x 36 discs
        "roles": [("a", "A / ✕ (bottom)"), ("b", "B / ○ (right)"), ("x", "X / □ (left)"), ("y", "Y / △ (top)")],
        "summary_roles": ("a", "b", "x", "y"),
    },
    "41010": {
        "title": "Serious Sam HD: The Second Encounter",
        "pad_art": "masks/serious/ds3-hd-tse-512",
        "pad_clear": (27, 26, 27),
        # B / X / Y ovals were not among the real extracts (the Deck was asleep): assumed equal to the First Encounter's
        # (same art, A measured identical), see the game page
        "oval": {"a": (5, 4, 25, 28), "b": (7, 4, 27, 28), "x": (7, 4, 27, 28), "y": (5, 4, 25, 28)},
        "box": {"a": (7, 7, 56, 57), "b": (7, 7, 56, 57), "x": (7, 7, 56, 57), "y": (7, 7, 56, 57),    # 49 x 50 discs
                "start": (7, 3, 57, 61), "select": (7, 3, 57, 61),                                       # 50 x 58
                "lb": (4, 13, 59, 51), "rb": (4, 13, 59, 51),                                            # 55 x 38
                "lt": (10, 4, 54, 59), "rt": (10, 4, 54, 59),                                            # 44 x 55
                "l3": (1, 6, 62, 58), "r3": (1, 6, 62, 58)},                                             # 61 x 52
        "roles": [("a", "A / ✕ (bottom)"), ("b", "B / ○ (right)"), ("x", "X / □ (left)"), ("y", "Y / △ (top)"),
                  ("lb", "LB / L1"), ("rb", "RB / R1"), ("lt", "LT / L2"), ("rt", "RT / R2"),
                  ("l3", "Stick click / L3 - unconfirmed"), ("select", "Back / SHARE"), ("start", "Start / OPTIONS")],
    },
}


def key_of(row):
    """A table row's key: its file name; the TSE copies under SeriousSamHD_TSE/ get a prefix (the names repeat)."""
    parts = row["id"].split("/")
    return ("TSE_" if parts[0].endswith("_TSE") else "") + parts[-1]


def entry(row):
    """The fields the overlay strategy needs from one generated table row."""
    return {"key": key_of(row), "path": row["path"], "size": row["size"], "sha256": row["sha256"],
            "crc32": row["crc32"], "gro": row["gro"], "width": row["width"], "height": row["height"],
            "fmt": row["fmt"]}


def symbol(spec, row):
    """The builder that draws one prompt row: a round symbol on an oval / disc, or a shape fitted into its box."""
    role = row["role"]
    oval = row["fmt"] == "rgba8"
    box = spec["oval"][role] if oval else spec["box"][role]
    fit = "round" if role in ROUND_ROLES else "box"
    return RoundSymbol(ROLE_ART[role], box, "bleed", None, fit)


def make_profile(appid, vehicle="gro", pad_picture=True, **overrides):
    spec, table = SPECS[appid], serious_tables.GAMES[appid]
    rows = [r for r in table["textures"] if r["action"] == "replace" and r["kind"] == "button"]
    wanted = {r for r in spec["box"]} | set(spec["oval"])
    entries, builders = [], {}
    for row in sorted(rows, key=key_of):
        if row["role"] not in wanted:
            raise ValueError("%s: no box measured for role %s" % (key_of(row), row["role"]))
        entries.append(entry(row))
        builders[key_of(row)] = symbol(spec, row)
    if pad_picture:
        pad = next(r for r in table["textures"] if r["action"] == "pad_art")
        entries.append(entry(pad))
        builders[key_of(pad)] = WholePicture(spec["pad_art"], spec["pad_clear"])
    atlas = next(r for r in table["textures"] if r["action"] == "replace_part")
    cells = []
    for row in sorted(rows, key=key_of):
        part = row.get("atlas_part")
        if part:
            x, y, w, h = part["rect"]
            if part["group"] != 0:
                raise ValueError("%s: atlas group %r is not the first block" % (key_of(row), part["group"]))
            cells.append((x, y, w, h, builders[key_of(row)]))
    entries.append(entry(atlas))
    builders[key_of(atlas)] = AtlasCells(0, cells)
    args = dict(
        appid=appid,
        name=spec["title"],
        entries=entries,
        builders=builders,
        mount=table["mount"],
        load_order=[(n, size) for n, _, size in table["load_order"]],
        loose_root=".",
        archive=ARCHIVE,
        vehicle=vehicle,
        revision=1,
        native_style="xbox",
        roles=spec["roles"],
        original_hint="The game's own Xbox prompts (white glyphs, glossy A/B/X/Y ovals). "
                      "\"Xbox\" and \"Original\" are the same thing here.",
        notes="Adds one small archive (zz_ButtonGlyphs.gro) next to the game's own; nothing of the game is modified, "
              "Original deletes it. The game's archives are signed: if the game ignores the extra archive the "
              "profile has to switch to loose files.",
        support="partial",
    )
    args.update(overrides)
    return OverlayGlyphProfile(**args)
