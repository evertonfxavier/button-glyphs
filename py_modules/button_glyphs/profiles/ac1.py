"""Assassin's Creed (2008, Director's Cut) - Steam appid 15100.

The PC version draws face-button prompts as four body-part glyphs in Xbox 360
colours (head = Y/yellow, weapon hand = X/blue, empty hand = B/red, legs = A/green).
They live in DataPC.forge, entry "Game Bootstrap Settings", in four DXT3 textures
without mipmaps: two coloured font atlases, one white font atlas and one HUD icon.

All four textures come from TGA files and store their rows bottom-up, so icons
are drawn mirrored vertically (flip_y) to show upright in game.
"""
from .forge_glyphs import ForgeGlyphProfile

PROFILE = ForgeGlyphProfile(
    appid="15100",
    name="Assassin's Creed",
    file="DataPC.forge",
    revision=2,
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
    notes="Patches DataPC.forge (~200 MB); the original is backed up first.",
)
