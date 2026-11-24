"""Assassin's Creed (2008, Director's Cut) - Steam appid 15100.

The PC version draws face-button prompts as four body-part glyphs in Xbox 360
colours (head = Y/yellow, weapon hand = X/blue, empty hand = B/red, legs = A/green).
They live in DataPC.forge, entry "Game Bootstrap Settings", in four DXT3 textures
without mipmaps: two coloured font atlases, one white font atlas and one HUD icon.

All four textures come from TGA files and store their rows bottom-up, so icons
are drawn mirrored vertically (flip_y) to show upright in game.
"""
from .forge_glyphs import ForgeGlyphProfile

# --- Xbox 360 button art shipped in the area archives ("Conrtols" is how the game spells it).
# 32x32 DXT3, bottom-up rows. White "_Map" and coloured "_Color_Map" variants exist for
# A/B/X/Y; they are already Xbox, so only the PlayStation style redraws them. The "Back"
# texture is a plain disc whose use we could not pin down, so it is left alone.
FACE = [  # (resource name stem, id, white icon, coloured icon)
    ("A", 0x1DEA5162, 0x1DEA55D2, "plain-cross", "outline-blue-cross"),
    ("B", 0x1DEA5168, 0x1DEA55C4, "plain-circle", "outline-red-circle"),
    ("X", 0x1DEA517A, 0x1DEA55CA, "plain-square", "outline-purple-square"),
    ("Y", 0x1DEA5180, 0x1DEA55B8, "plain-triangle", "outline-green-triangle"),
]
PAD = [  # (id, resource name, width, height, role, PlayStation icon) - Animus room only
    (0x1DEA5544, "Conrtols_LeftJoystick_Map", 32, 32, "ls", "plain-L"),
    (0x1DEA554A, "Conrtols_RightJoystick_Map", 32, 32, "rs", "plain-R"),
    (0x1DEA5556, "Conrtols_LeftTrigger_Map", 32, 64, "lt", "plain-L2"),
    (0x1DEA5550, "Conrtols_RightTrigger_Map", 32, 64, "rt", "plain-R2"),
]
# area archive -> forge entry that holds its textures (Present_Room has no coloured set)
AREAS = {
    "DataPC_Acre.forge": "Cell01364_DataBlock",
    "DataPC_Arsuf.forge": "Cell21844_DataBlock",
    "DataPC_Assassination_Talal.forge": "Cell01364_DataBlock",
    "DataPC_Damascus.forge": "Cell01364_DataBlock",
    "DataPC_Flashback_Paradise.forge": "Cell00340_DataBlock",
    "DataPC_Jerusalem.forge": "Cell01364_DataBlock",
    "DataPC_Kingdom.forge": "Cell05460_DataBlock",
    "DataPC_Masyaf.forge": "Cell05460_DataBlock",
    "DataPC_Memory_Corridor.forge": "Cell00084_DataBlock",
    "DataPC_Present_Room.forge": "Cell00084_DataBlock",
    "DataPC_SolomonTemple.forge": "Cell00340_DataBlock",
}


def _button(tid, name, w, h, role, icon):
    """One single-glyph texture: wiped and redrawn with `icon` (PlayStation style only)."""
    return {
        "id": tid, "name": name, "label": name, "width": w, "height": h, "format": "DXT3",
        "flip_y": True, "clear_all": True, "verify": "name",
        "boxes": {role: (1, 1, w - 2, h - 2)},
        "icons": {"ps": {role: "ps/" + icon}},
    }


def _area_target(file, entry):
    textures = []
    for stem, white, colour, white_icon, colour_icon in FACE:
        role = stem.lower()
        textures.append(_button(white, "Conrtols_%s_Map" % stem, 32, 32, role, white_icon))
        if file != "DataPC_Present_Room.forge":
            textures.append(_button(colour, "Conrtols_%s_Color_Map" % stem, 32, 32, role, colour_icon))
    if file == "DataPC_Present_Room.forge":
        textures += [_button(*row) for row in PAD]
    return {"file": file, "entry": entry, "styles": ("ps",), "textures": textures}


PROFILE = ForgeGlyphProfile(
    appid="15100",
    support="partial",
    name="Assassin's Creed",
    file="DataPC.forge",
    revision=3,
    datafile="Game Bootstrap Settings",
    roles=[
        ("head", "Head (Eagle Vision)"),
        ("weapon", "Weapon hand"),
        ("empty", "Empty hand"),
        ("legs", "Legs"),
    ],
    textures=[
        {
            "id": 0x4719595F,
            "label": "AnimusText Bold_PC font",
            "width": 256, "height": 512, "format": "DXT3",
            "flip_y": True,
            "boxes": {"weapon": (3, 66, 35, 98), "empty": (3, 104, 35, 136),
                      "legs": (3, 142, 35, 174), "head": (3, 180, 35, 212)},
        },
        {
            "id": 0x47195956,
            "label": "AnimusText PC font",
            "width": 256, "height": 256, "format": "DXT3",
            "flip_y": True,
            "boxes": {"weapon": (28, 79, 49, 101), "empty": (28, 105, 49, 127),
                      "legs": (28, 131, 49, 153), "head": (53, 149, 74, 171)},
        },
        {
            "id": 0x47195991,
            "label": "AnimusTechno Regular 22_PC font (white)",
            "width": 64, "height": 1024, "format": "DXT3",
            "flip_y": True,
            "tint": (255, 255, 255),
            "boxes": {"weapon": (2, 579, 26, 602), "empty": (2, 606, 26, 629),
                      "legs": (2, 633, 26, 656), "head": (2, 688, 26, 712)},
            "icons": {"xbox": {"head": "xbox/y-outlined", "weapon": "xbox/x-outlined",
                               "empty": "xbox/b-outlined", "legs": "xbox/a-outlined"}},
        },
        {
            "id": 0x1DEA45F6,
            "label": "ControlsHUD_ButtonA_2 (Xbox 360 A button)",
            "width": 64, "height": 64, "format": "DXT3",
            "flip_y": True,
            "clear_all": True,
            "boxes": {"legs": (21, 22, 42, 42)},
        },
    ],
    styles={
        "ps": {"head": "ps/outline-green-triangle", "weapon": "ps/outline-purple-square",
               "empty": "ps/outline-red-circle", "legs": "ps/outline-blue-cross"},
        "xbox": {"head": "xbox/y-filled-yellow", "weapon": "xbox/x-filled-blue",
                 "empty": "xbox/b-filled-red", "legs": "xbox/a-filled-green"},
    },
    extra_targets=[_area_target(f, e) for f, e in AREAS.items()],
    notes=("Patches the font atlases in DataPC.forge and, for PlayStation, the Xbox 360 button "
           "textures in the 11 area archives. Only the changed entries are rewritten; the "
           "original data stays inside the files."),
    original_hint="The game's own icons (body-part glyphs in Xbox 360 colours).",
)
